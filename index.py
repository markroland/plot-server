#!/usr/bin/env python
#
# AxiDraw Plot Server
# This is designed to be connected to a single plotter
#
# Run in background even after a hang up:
#  nohup python index.py > /dev/null 2>&1 &

from dotenv import load_dotenv
from io import BytesIO
import csv
import hashlib
import json
import threading
import time
from pyaxidraw import axidraw
from flask import Flask, request, Response, render_template, send_file
from flask_cors import CORS
import os
from plotter_service import plot, preview_plot, toggle_servo
from plotter_status import PlotterStatusService
from preview_parser import parse_plot_output, parse_preview_output
from svg_library import (
    build_file_entry,
    build_thumbnail_relative_path,
    generate_svg_pdf_bytes,
    generate_svg_thumbnail,
)

# Load settings from environment
load_dotenv()


# Set up a Semaphore object for use with blocking plot
# requests while the plotter is busy
sem = threading.Semaphore()
plot_state_lock = threading.Lock()
runtime_plot_state = {
    "is_plotting": False,
    "stop_requested": False,
    "last_stop": {
        "requested_at": None,
        "success": None,
        "servo_state": "unknown",
    },
}

# Create an AxiDraw class instance
ad = axidraw.AxiDraw()
status_ad = axidraw.AxiDraw()
status_service = PlotterStatusService(status_ad, sem)

# Create new Flask app
app = Flask(__name__)
APP_VERSION = "1.2.0"

# Enable CORS
CORS(app, resources={r"/*": {"origins": "*"}})

# Get the directory of the current script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Example: Define the upload folder relative to the script
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')

art_dir = app.config['UPLOAD_FOLDER']
LOG_DIR = os.path.join(BASE_DIR, 'log')
PLOT_LOG_FILE = os.path.join(LOG_DIR, 'plot-log.jsonl')

TOOLS_CSV_PATH = os.path.join(BASE_DIR, 'tools.csv')
MATERIAL_CSV_PATH = os.path.join(BASE_DIR, 'material.csv')
plot_log_file_lock = threading.Lock()


def load_csv_options(file_path):
    """Load one option per line from a CSV file (first column only)."""
    options = []

    if not os.path.exists(file_path):
        print(f"[WARN] Options CSV not found: {file_path}")
        return options

    try:
        with open(file_path, newline='', encoding='utf-8') as csv_file:
            reader = csv.reader(csv_file)
            for row in reader:
                if not row:
                    continue

                value = row[0].strip()
                if not value:
                    continue

                # Keep None as an HTML default option only.
                if value.lower() == 'none':
                    continue

                options.append(value)
    except OSError as error:
        print(f"[WARN] Failed to read options CSV {file_path}: {error}")

    return options


TOOL_OPTIONS = load_csv_options(TOOLS_CSV_PATH)
MATERIAL_OPTIONS = load_csv_options(MATERIAL_CSV_PATH)


def set_runtime_plot_state(*, is_plotting=None, stop_requested=None, last_stop=None):
    """Update process-local plotting state used by API responses and controls."""
    with plot_state_lock:
        if is_plotting is not None:
            runtime_plot_state["is_plotting"] = is_plotting
        if stop_requested is not None:
            runtime_plot_state["stop_requested"] = stop_requested
        if last_stop is not None:
            runtime_plot_state["last_stop"] = last_stop


def get_runtime_plot_state_snapshot():
    """Return a copy of the in-memory plotting state for response payloads."""
    with plot_state_lock:
        return {
            "is_plotting": runtime_plot_state["is_plotting"],
            "stop_requested": runtime_plot_state["stop_requested"],
            "last_stop": dict(runtime_plot_state["last_stop"]),
        }


def apply_runtime_state_to_status(status_data):
    """Merge local runtime controls into hardware status payload."""
    runtime_state = get_runtime_plot_state_snapshot()

    if runtime_state["is_plotting"]:
        status_data["status"] = "busy"
        status_data["plot_state"] = "plotting"
    else:
        status_data["plot_state"] = "idle"

    status_data["stop_requested"] = runtime_state["stop_requested"]
    status_data["last_stop"] = runtime_state["last_stop"]
    return status_data


def run_stop_cleanup_commands(model_number):
    """Best-effort stop cleanup: command pen up first, then disable XY motors."""
    stop_ad = axidraw.AxiDraw()

    result = {
        "raise_pen": False,
        "disable_xy": False,
        "servo_state": "unknown",
    }

    stop_ad.options.model = model_number
    stop_ad.plot_setup()
    stop_ad.options.preview = False
    stop_ad.options.mode = "manual"

    stop_ad.options.manual_cmd = "raise_pen"
    stop_ad.plot_run()
    result["raise_pen"] = True
    result["servo_state"] = "commanded_up"

    stop_ad.options.manual_cmd = "disable_xy"
    stop_ad.plot_run()
    result["disable_xy"] = True

    return result


def get_active_model_number():
    """Prefer detected hardware model; fall back to configured environment default."""
    try:
        return status_service.detect_connected_model_number()
    except Exception as error:
        fallback_model = status_service.get_default_model_number()
        print(f"[WARN] Falling back to configured AxiDraw model {fallback_model}: {error}")
        return fallback_model

def resolve_artwork_path(relative_path):
    """Resolve a user-supplied artwork path within the configured art directory."""
    art_dir_path = os.path.abspath(art_dir)
    candidate_path = os.path.abspath(os.path.join(art_dir_path, relative_path))

    try:
        if os.path.commonpath([art_dir_path, candidate_path]) != art_dir_path:
            return None
    except ValueError:
        return None

    return candidate_path


def remove_empty_parent_directories(path, stop_dir):
    """Remove empty parent directories until reaching the configured stop directory."""
    current_dir = os.path.dirname(path)
    stop_dir = os.path.abspath(stop_dir)

    while current_dir.startswith(stop_dir) and current_dir != stop_dir:
        try:
            os.rmdir(current_dir)
        except OSError:
            break
        current_dir = os.path.dirname(current_dir)


def get_file_added_timestamp(path):
    """Return the file creation time when available, otherwise the modification time."""
    file_stats = os.stat(path)
    return getattr(file_stats, 'st_birthtime', file_stats.st_mtime)


def calculate_file_md5(path):
    """Calculate a file md5 hash for logging and traceability."""
    file_hash = hashlib.md5()
    with open(path, 'rb') as file_handle:
        for chunk in iter(lambda: file_handle.read(8192), b''):
            file_hash.update(chunk)
    return file_hash.hexdigest()


def format_log_timestamp(timestamp):
    """Return a stable local timestamp format for log rows."""
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))


def append_plot_log_entry(entry):
    """Append one JSON log entry to disk so log history survives restarts."""
    os.makedirs(LOG_DIR, exist_ok=True)

    with plot_log_file_lock:
        with open(PLOT_LOG_FILE, 'a', encoding='utf-8') as log_file:
            log_file.write(json.dumps(entry, ensure_ascii=True) + '\n')


def load_plot_log_entries(limit=300):
    """Load persisted plot log entries from disk (newest first)."""
    if not os.path.exists(PLOT_LOG_FILE):
        return []

    entries = []
    with plot_log_file_lock:
        with open(PLOT_LOG_FILE, 'r', encoding='utf-8') as log_file:
            for line in log_file:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return list(reversed(entries[-limit:]))


def clear_plot_log_entries():
    """Clear persisted plot log entries."""
    os.makedirs(LOG_DIR, exist_ok=True)

    with plot_log_file_lock:
        with open(PLOT_LOG_FILE, 'w', encoding='utf-8') as log_file:
            log_file.write('')

# Define route: Default
@app.route('/')
def index():
    """Render the main page with the available SVG files sorted newest first."""

    # Recursively get all .svg files in art_dir and subdirectories
    plot_files = []
    for root, dirs, files in os.walk(art_dir):
        for f in files:
            if f.lower().endswith('.svg'):
                # Store relative path from art_dir
                absolute_path = os.path.join(root, f)
                rel_path = os.path.relpath(absolute_path, art_dir)
                plot_files.append((get_file_added_timestamp(absolute_path), build_file_entry(rel_path)))

    plot_files.sort(key=lambda item: (-item[0], item[1]['filename'].lower()))
    plot_files = [entry for _, entry in plot_files]

    return render_template(
        'index.html',
        files=plot_files,
        art_dir=art_dir,
        app_version=APP_VERSION,
        tool_options=TOOL_OPTIONS,
        material_options=MATERIAL_OPTIONS,
    )

# Define route for a plot request
@app.route('/plot/<path:file>', methods=['GET', 'POST'])
def plot_request(file):
    """Handle preview, plotting, and SVG upload requests for a given file path."""

    if request.method == 'GET':

        filepath = resolve_artwork_path(file)

        # Make sure the file exists
        if not filepath or not os.path.exists(filepath):

            response = 'File Not Found', 404

            return response

        # If the file is found, acquire a Semaphore to block
        # other incoming requests until the plotter is done
        if sem.acquire(True, 0.1):
            try:
                if request.args.get("preview", "").lower() == "true":
                    preview_layer = request.args.get("layer", default=0, type=int)
                    model_number = get_active_model_number()
                    preview_output = preview_plot(ad, filepath, preview_layer, model_number)
                    preview_data = parse_preview_output(preview_output)
                    return Response(json.dumps(preview_data), mimetype='application/json')

                # Determine requested layer
                layer = request.args.get("layer", default=0, type=int)
                title = request.args.get('title', default='', type=str)
                tool = request.args.get('tool', default='None', type=str)
                media = request.args.get('media', default='None', type=str)
                format_value = request.args.get('format', default='', type=str)
                orientation = request.args.get('orientation', default='', type=str)
                edition = request.args.get('edition', default=1, type=int)
                editions = request.args.get('editions', default=1, type=int)
                model_number = get_active_model_number()
                set_runtime_plot_state(is_plotting=True, stop_requested=False)
                started_at = int(time.time())
                plot_output = plot(ad, filepath, layer, model_number)
                completed_at = int(time.time())

                try:
                    plot_metrics = parse_plot_output(plot_output)
                except ValueError:
                    plot_metrics = {
                        'plot_duration': max(0, completed_at - started_at),
                        'plot_path': 0.0,
                        'plot_travel': 0.0,
                        'lifts': 0,
                    }

                response_payload = {
                    'status': 'ok',
                    'layer': layer,
                    'title': title,
                    'file': file,
                    'filepath': filepath,
                    'filename': os.path.basename(filepath),
                    'file_hash': calculate_file_md5(filepath),
                    'plotter': status_service.get_plotter_name(),
                    'tool': tool,
                    'media': media,
                    'format': format_value,
                    'orientation': orientation,
                    'edition': edition,
                    'editions': editions,
                    'model_number': model_number,
                    'started_at': started_at,
                    'completed_at': completed_at,
                    'metrics': plot_metrics,
                }

                append_plot_log_entry({
                    'time': format_log_timestamp(completed_at),
                    'status': 'ok',
                    'title': title,
                    'filename': response_payload['filename'],
                    'fileHash': response_payload['file_hash'],
                    'plotter': response_payload['plotter'],
                    'edition': f"{edition}/{editions}",
                    'layer': str(layer) if layer and layer > 0 else 'all',
                    'tool': tool,
                    'media': media,
                    'format': format_value,
                    'orientation': orientation,
                    'duration': plot_metrics.get('plot_duration', 0),
                    'path': plot_metrics.get('plot_path', 0.0),
                    'travel': plot_metrics.get('plot_travel', 0.0),
                    'lifts': plot_metrics.get('lifts', 0),
                })

                response = Response(json.dumps(response_payload), mimetype='application/json')
            except Exception as e:
                print(f"[ERROR] Exception during plot: {e}")
                response = f'Error: {e}', 500
            finally:
                set_runtime_plot_state(is_plotting=False)
                sem.release()
        else:
            response = 'Busy', 503
        return response

    if request.method == 'POST':

        if 'file' not in request.files:
            return 'No file part', 400

        # plot an uploaded file
        uploaded_file = request.files.get('file')

        if uploaded_file.filename == '':
            return 'No selected file', 400

        filename = os.path.basename(uploaded_file.filename)

        # Save the uploaded file relative to the script
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        uploaded_file.save(filepath)

        thumbnail_relative_path = build_thumbnail_relative_path(filename)
        thumbnail_path = os.path.join(app.config['UPLOAD_FOLDER'], thumbnail_relative_path)
        try:
            generate_svg_thumbnail(filepath, thumbnail_path)
        except Exception as error:
            print(f"[WARN] Failed to generate thumbnail for {filename}: {error}")

        return '', 200

@app.route('/download/<path:file>')
def download_pdf(file):
    """Generate and return a PDF download for the requested SVG file."""
    filepath = resolve_artwork_path(file)
    if not filepath or not os.path.exists(filepath):
        return 'File Not Found', 404

    if not filepath.lower().endswith('.svg'):
        return 'Unsupported file type', 400

    try:
        pdf_bytes = generate_svg_pdf_bytes(filepath)
    except Exception as error:
        print(f"[WARN] Failed to generate PDF for {file}: {error}")
        return 'Failed to generate PDF', 500

    download_name = f"{os.path.splitext(os.path.basename(file))[0]}.pdf"
    return send_file(
        BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=download_name,
    )


@app.route('/files/<path:file>', methods=['DELETE'])
def delete_file(file):
    """Delete an SVG file and its generated thumbnail from the artwork library."""
    filepath = resolve_artwork_path(file)
    if not filepath or not os.path.exists(filepath):
        return Response(json.dumps({'error': 'File Not Found'}), status=404, mimetype='application/json')

    if not filepath.lower().endswith('.svg'):
        return Response(json.dumps({'error': 'Unsupported file type'}), status=400, mimetype='application/json')

    thumbnail_path = os.path.join(app.config['UPLOAD_FOLDER'], build_thumbnail_relative_path(file))

    try:
        os.remove(filepath)
        remove_empty_parent_directories(filepath, art_dir)

        if os.path.exists(thumbnail_path):
            os.remove(thumbnail_path)
            remove_empty_parent_directories(thumbnail_path, app.config['UPLOAD_FOLDER'])
    except OSError as error:
        print(f"[WARN] Failed to delete file {file}: {error}")
        return Response(json.dumps({'error': 'Failed to delete file'}), status=500, mimetype='application/json')

    return Response(json.dumps({'deleted': file}), mimetype='application/json')

@app.route('/status')
def status():
    """Original status endpoint - returns plain text for backwards compatibility"""
    status_data = apply_runtime_state_to_status(status_service.get_plotter_status())

    # Return plain text status for backwards compatibility
    status_text = status_data["status"]

    response = Response(status_text, mimetype='text/plain')

    # Set headers to prevent caching
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, public, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'

    return response

@app.route('/status.json')
def status_json():
    """JSON status endpoint - returns detailed machine info"""
    status_data = apply_runtime_state_to_status(status_service.get_plotter_status())

    response = Response(json.dumps(status_data), mimetype='application/json')

    # Set headers to prevent caching
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, public, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'

    return response


@app.route('/logs.json', methods=['GET', 'DELETE'])
def logs_json():
    """Read or clear persisted plot log entries."""
    if request.method == 'DELETE':
        clear_plot_log_entries()
        return Response(json.dumps({'status': 'ok'}), mimetype='application/json')

    return Response(json.dumps({'entries': load_plot_log_entries()}), mimetype='application/json')


@app.route('/plot/stop', methods=['POST'])
def stop_plot():
    """Best-effort plot interruption and cleanup commands."""
    runtime_state = get_runtime_plot_state_snapshot()
    if not runtime_state["is_plotting"]:
        return Response(json.dumps({'error': 'No active plot'}), status=409, mimetype='application/json')

    set_runtime_plot_state(stop_requested=True)

    model_number = get_active_model_number()
    stop_result = {
        "requested_at": int(time.time()),
        "success": False,
        "servo_state": "unknown",
        "raise_pen": False,
        "disable_xy": False,
        "error": None,
    }

    try:
        command_result = run_stop_cleanup_commands(model_number)
        stop_result.update(command_result)
        stop_result["success"] = bool(command_result["raise_pen"] and command_result["disable_xy"])
    except Exception as error:
        print(f"[ERROR] Stop plot command failed: {error}")
        stop_result["error"] = str(error)

    set_runtime_plot_state(last_stop=stop_result)

    append_plot_log_entry({
        'time': format_log_timestamp(stop_result['requested_at']),
        'status': 'stopped' if stop_result['success'] else 'error',
        'title': '',
        'filename': '',
        'file': '',
        'fileHash': '-',
        'plotter': status_service.get_plotter_name(),
        'edition': '-',
        'layer': '-',
        'tool': '-',
        'media': '-',
        'format': '-',
        'orientation': '-',
        'duration': '-',
        'path': '-',
        'travel': '-',
        'lifts': '-',
    })

    if not stop_result["success"]:
        return Response(json.dumps({
            'error': stop_result["error"] or 'Failed to stop plot cleanly',
            'stop_result': stop_result,
        }), status=500, mimetype='application/json')

    return Response(json.dumps({
        'status': 'ok',
        'stop_result': stop_result,
    }), mimetype='application/json')


@app.route('/servo/toggle', methods=['POST'])
def servo_toggle():
    """Toggle the AxiDraw servo pen state (up/down)."""
    if not sem.acquire(True, 1.0):
        return Response(json.dumps({'error': 'Busy'}), status=503, mimetype='application/json')

    try:
        model_number = get_active_model_number()
        servo_ad = axidraw.AxiDraw()
        print(f"[INFO] Servo toggle request: model={model_number}")
        toggle_servo(servo_ad, model_number)
        print("[INFO] Servo toggle command completed")
        return Response(json.dumps({'status': 'ok'}), mimetype='application/json')
    except Exception as error:
        print(f"[ERROR] Exception during servo toggle: {error}")
        return Response(json.dumps({'error': str(error)}), status=500, mimetype='application/json')
    finally:
        sem.release()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("HOST_PORT", 5007)))