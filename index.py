#!/usr/bin/env python
#
# AxiDraw Plot Server
# This is designed to be connected to a single plotter
#
# Run in background even after a hang up:
#  nohup python index.py > /dev/null 2>&1 &

from dotenv import load_dotenv
from io import BytesIO
import json
import threading
from pyaxidraw import axidraw
from flask import Flask, request, Response, render_template, send_file
from flask_cors import CORS
import os
from plotter_service import plot, preview_plot
from plotter_status import PlotterStatusService
from preview_parser import parse_preview_output
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

# Create an AxiDraw class instance
ad = axidraw.AxiDraw()
status_service = PlotterStatusService(ad, sem)

# Create new Flask app
app = Flask(__name__)

# Enable CORS
CORS(app, resources={r"/*": {"origins": "*"}})

# Get the directory of the current script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Example: Define the upload folder relative to the script
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')


def resolve_artwork_path(relative_path):
    art_dir = os.environ.get("ART_DIRECTORY")
    if not art_dir:
        return None

    art_dir_path = os.path.abspath(art_dir)
    candidate_path = os.path.abspath(os.path.join(art_dir_path, relative_path))

    try:
        if os.path.commonpath([art_dir_path, candidate_path]) != art_dir_path:
            return None
    except ValueError:
        return None

    return candidate_path


def remove_empty_parent_directories(path, stop_dir):
    current_dir = os.path.dirname(path)
    stop_dir = os.path.abspath(stop_dir)

    while current_dir.startswith(stop_dir) and current_dir != stop_dir:
        try:
            os.rmdir(current_dir)
        except OSError:
            break
        current_dir = os.path.dirname(current_dir)

# Define route: Default
@app.route('/')
def index():

    # Recursively get all .svg files in art_dir and subdirectories
    art_dir = os.environ.get("ART_DIRECTORY")
    plot_files = []
    for root, dirs, files in os.walk(art_dir):
        for f in files:
            if f.lower().endswith('.svg'):
                # Store relative path from art_dir
                rel_path = os.path.relpath(os.path.join(root, f), art_dir)
                plot_files.append(build_file_entry(rel_path))

    plot_files.sort(key=lambda item: item['filename'].lower())

    return render_template('index.html', files=plot_files, art_dir=art_dir)

# Define route for a plot request
@app.route('/plot/<path:file>', methods=['GET', 'POST'])
def plot_request(file):

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
                    preview_output = preview_plot(ad, filepath)
                    preview_data = parse_preview_output(preview_output)
                    return Response(json.dumps(preview_data), mimetype='application/json')

                # Determine requested layer
                layer = request.args.get("layer", default=0, type=int)
                model_number = int(os.environ.get("AXIDRAW_MODEL", "4"))
                plot(ad, filepath, layer, model_number)
                response = 'Done: ' + str(layer)
            except Exception as e:
                print(f"[ERROR] Exception during plot: {e}")
                response = f'Error: {e}', 500
            finally:
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

        # Plot the uploaded file
        # if sem.acquire(True, 0.1):
        #     try:
        #         layer = request.args.get("layer", default=0, type=int)
        #         plot(filepath, layer)
        #         # os.remove(filepath)
        #         return f'Done: {layer}', 200
        #     except Exception as e:
        #         print(f"[ERROR] Exception during plot: {e}")
        #         return f'Error: {e}', 500
        #     finally:
        #         sem.release()
        # else:
        #     return 'Busy', 503

        return '', 200


@app.route('/download/<path:file>')
def download_pdf(file):
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
    filepath = resolve_artwork_path(file)
    if not filepath or not os.path.exists(filepath):
        return Response(json.dumps({'error': 'File Not Found'}), status=404, mimetype='application/json')

    if not filepath.lower().endswith('.svg'):
        return Response(json.dumps({'error': 'Unsupported file type'}), status=400, mimetype='application/json')

    thumbnail_path = os.path.join(app.config['UPLOAD_FOLDER'], build_thumbnail_relative_path(file))

    try:
        os.remove(filepath)
        remove_empty_parent_directories(filepath, os.environ.get("ART_DIRECTORY"))

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
    status_data = status_service.get_plotter_status()

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
    status_data = status_service.get_plotter_status()

    response = Response(json.dumps(status_data), mimetype='application/json')

    # Set headers to prevent caching
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, public, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'

    return response

# Set up cross origin resource sharing
# @app.after_request
# def after_request(response):
#     response.headers.add('Access-Control-Allow-Origin', 'http://project.markroland.com')
#     response.headers.add('Access-Control-Allow-Methods', 'GET, OPTIONS, POST, PUT')
#     return response

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=os.environ.get("HOST_PORT"))