#!/usr/bin/env python
#
# AxiDraw Plot Server
# This is designed to be connected to a single plotter
#
# Run in background even after a hang up:
#  nohup python index.py > /dev/null 2>&1 &

from dotenv import load_dotenv
import threading
from pyaxidraw import axidraw
from flask import Flask, request, Response, render_template
from flask_cors import CORS
import os
import importlib.util

# Load settings from environment
load_dotenv()

def load_axidraw_config(config_path):
    """Load and parse AxiDraw configuration file"""
    if not config_path or not os.path.exists(config_path):
        return {}

    try:
        # Load the Python config file as a module
        spec = importlib.util.spec_from_file_location("axidraw_config", config_path)
        config_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config_module)

        # Extract key configuration parameters
        config_data = {}

        # Basic movement settings
        if hasattr(config_module, 'speed_pendown'):
            config_data['speed_pendown'] = config_module.speed_pendown
        if hasattr(config_module, 'speed_penup'):
            config_data['speed_penup'] = config_module.speed_penup
        if hasattr(config_module, 'accel'):
            config_data['accel'] = config_module.accel

        # Pen position settings
        if hasattr(config_module, 'pen_pos_up'):
            config_data['pen_pos_up'] = config_module.pen_pos_up
        if hasattr(config_module, 'pen_pos_down'):
            config_data['pen_pos_down'] = config_module.pen_pos_down

        # Pen rate settings
        if hasattr(config_module, 'pen_rate_raise'):
            config_data['pen_rate_raise'] = config_module.pen_rate_raise
        if hasattr(config_module, 'pen_rate_lower'):
            config_data['pen_rate_lower'] = config_module.pen_rate_lower

        # Model and hardware settings
        if hasattr(config_module, 'model'):
            config_data['model'] = config_module.model
        if hasattr(config_module, 'const_speed'):
            config_data['const_speed'] = config_module.const_speed
        if hasattr(config_module, 'auto_rotate'):
            config_data['auto_rotate'] = config_module.auto_rotate
        if hasattr(config_module, 'reordering'):
            config_data['reordering'] = config_module.reordering

        # Advanced settings
        if hasattr(config_module, 'pen_delay_down'):
            config_data['pen_delay_down'] = config_module.pen_delay_down
        if hasattr(config_module, 'pen_delay_up'):
            config_data['pen_delay_up'] = config_module.pen_delay_up
        if hasattr(config_module, 'resolution'):
            config_data['resolution'] = config_module.resolution

        # Travel dimensions for different machine types
        travel_dimensions = {}
        if hasattr(config_module, 'x_travel_default'):
            travel_dimensions['x_travel_default'] = config_module.x_travel_default
        if hasattr(config_module, 'y_travel_default'):
            travel_dimensions['y_travel_default'] = config_module.y_travel_default
        if hasattr(config_module, 'x_travel_V3A3'):
            travel_dimensions['x_travel_V3A3'] = config_module.x_travel_V3A3
        if hasattr(config_module, 'y_travel_V3A3'):
            travel_dimensions['y_travel_V3A3'] = config_module.y_travel_V3A3
        if hasattr(config_module, 'x_travel_V3XLX'):
            travel_dimensions['x_travel_V3XLX'] = config_module.x_travel_V3XLX
        if hasattr(config_module, 'y_travel_V3XLX'):
            travel_dimensions['y_travel_V3XLX'] = config_module.y_travel_V3XLX
        if hasattr(config_module, 'x_travel_MiniKit'):
            travel_dimensions['x_travel_MiniKit'] = config_module.x_travel_MiniKit
        if hasattr(config_module, 'y_travel_MiniKit'):
            travel_dimensions['y_travel_MiniKit'] = config_module.y_travel_MiniKit
        if hasattr(config_module, 'x_travel_SEA1'):
            travel_dimensions['x_travel_SEA1'] = config_module.x_travel_SEA1
        if hasattr(config_module, 'y_travel_SEA1'):
            travel_dimensions['y_travel_SEA1'] = config_module.y_travel_SEA1
        if hasattr(config_module, 'x_travel_SEA2'):
            travel_dimensions['x_travel_SEA2'] = config_module.x_travel_SEA2
        if hasattr(config_module, 'y_travel_SEA2'):
            travel_dimensions['y_travel_SEA2'] = config_module.y_travel_SEA2

        config_data['travel_dimensions'] = travel_dimensions

        return config_data

    except Exception as e:
        print(f"Error loading config from {config_path}: {e}")
        return {}

# Set up a Semaphore object for use with blocking plot
# requests while the plotter is busy
sem = threading.Semaphore()

# Create an AxiDraw class instance
ad = axidraw.AxiDraw()

# Create new Flask app
app = Flask(__name__)

# Enable CORS
CORS(app, resources={r"/*": {"origins": "*"}})

# Get the directory of the current script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Example: Define the upload folder relative to the script
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')

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
                plot_files.append(rel_path)

    return render_template('index.html', files=plot_files)

def plot(filepath, layer=0):

    # Load file & configure plot context
    ad.plot_setup(filepath)
    ad.options.mode = "plot"
    ad.options.model = int(os.environ.get("AXIDRAW_MODEL", "4"))
    ad.options.auto_rotate = False
    ad.options.reordering = 0
    ad.options.check_limits = True
    ad.options.clip_to_page = True
    # speed_pendown
    # speed_penup
    # accel
    # pen_pos_down
    # pen_pos_up
    # pen_rate_lower
    # pen_rate_raise
    # pen_delay_down
    # pen_delay_up
    # const_speed
    # model
    # port
    # port_config

    # Set configuration (Debugging)
    # config_path = os.environ.get("AXIDRAW_MODEL_4_CONFIG")
    # ad.load_config(config_path)
    # print(f"[DEBUG] Config path: {config_path}")
    # if config_path and os.path.exists(config_path):
    #     print(f"[DEBUG] Config file exists: {config_path}")
    #     try:
    #         ad.load_config(config_path)
    #         print(f"[DEBUG] Loaded config. Some ad.options values:")
    #         print(f"  speed_pendown: {getattr(ad.options, 'speed_pendown', None)}")
    #         print(f"  speed_penup: {getattr(ad.options, 'speed_penup', None)}")
    #         print(f"  accel: {getattr(ad.options, 'accel', None)}")
    #         print(f"  pen_pos_down: {getattr(ad.options, 'pen_pos_down', None)}")
    #         print(f"  pen_pos_up: {getattr(ad.options, 'pen_pos_up', None)}")
    #         print(f"  model: {getattr(ad.options, 'model', None)}")
    #     except Exception as e:
    #         print(f"[DEBUG] Exception during ad.load_config: {e}")
    # else:
    #     print(f"[DEBUG] Configuration File Not Found: {config_path}")

    if layer > 0:
        ad.options.mode = "layers"
        ad.options.layer = layer

    # TODO: Protect against going outside of plotter's bounds

    # Plot the file
    ad.plot_run()

    # Turn off motors
    ad.options.mode = "manual"
    ad.options.manual_cmd = "disable_xy"
    ad.plot_run()

# Define route for a plot request
@app.route('/plot/<file>', methods=['GET', 'POST'])
def plot_request(file):

    if request.method == 'GET':

        filepath = os.environ.get("ART_DIRECTORY") + "/" + file

        # Make sure the file exists
        if not os.path.exists(filepath):

            response = 'File Not Found', 404

            return response

        # If the file is found, acquire a Semaphore to block
        # other incoming requests until the plotter is done
        if sem.acquire(True, 0.1):

            # Determine requested layer
            layer = request.args.get("layer", default=0, type=int)

            plot(filepath, layer)

            # Release the Semaphore
            sem.release()

            response = 'Done: ' + str(layer)

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

        # Save the uploaded file relative to the script
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], uploaded_file.filename)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        uploaded_file.save(filepath)

        # Plot the uploaded file
        if sem.acquire(True, 0.1):
            layer = request.args.get("layer", default=0, type=int)
            plot(filepath, layer)
            # os.remove(filepath)
            sem.release()
            return f'Done: {layer}', 200
        else:
            return 'Busy', 503

def get_plotter_status():
    """Helper function to get plotter status and machine type"""
    # Default response
    status_data = {
        "status": "off",
        "machine": "none",
        "device_info": "none",
        "model_number": None,
        "config": {}
    }

    # See https://axidraw.com/doc/cli_api/#list_names
    # Note: This indicates USB connection and NOT power on
    ad.plot_setup()
    ad.options.mode = "manual"
    ad.options.manual_cmd = "list_names"
    ad.plot_run()
    axidraw_list = ad.name_list

    # Debug: Print the device list
    print(f"Debug - axidraw_list type: {type(axidraw_list)}")
    print(f"Debug - axidraw_list: {axidraw_list}")

    if axidraw_list is not None and len(axidraw_list) > 0:

        # Get first connected device
        device_identifier = axidraw_list[0]
        print(f"Debug - device_identifier: '{device_identifier}'")

        status_data["device_info"] = device_identifier

        # Determine machine type based on device identifier
        # This can be either a USB nickname or a port path
        machine_type = "Unknown"
        machine_model = None

        # Check if it's a USB port path (older firmware or no nickname)
        if "/dev/" in device_identifier or "COM" in device_identifier:
            # This is a USB port path, not a nickname
            # We can't determine machine type from port path alone
            machine_type = "AxiDraw (No nickname assigned)"
            # Use default model from environment
            machine_model = int(os.environ.get("AXIDRAW_MODEL", "4"))
            print(f"  Device uses port path: {device_identifier}")
            print(f"  Using default model: {machine_model}")
            print(f"  To identify machine type, assign a nickname using:")
            print(f"  axicli -m manual -M write_nameYourNicknameHere")
        else:
            # This appears to be a nickname - try to identify machine type
            nickname = device_identifier.lower()

            # Match based on nickname patterns (you can customize these)
            if "mini" in nickname or "mk" in nickname:
                machine_type = "MiniKit-v2"
                machine_model = 4
            elif "a3" in nickname or "se" in nickname or "large" in nickname:
                machine_type = "AxiDraw-A3/SE"
                machine_model = 2
            elif "xlx" in nickname:
                machine_type = "AxiDraw-XLX"
                machine_model = 3
            elif "v3" in nickname or "v2" in nickname:
                machine_type = "AxiDraw-V2/V3"
                machine_model = 1
            elif "a1" in nickname:
                machine_type = "AxiDraw-SE/A1"
                machine_model = 5
            elif "a2" in nickname:
                machine_type = "AxiDraw-SE/A2"
                machine_model = 6
            else:
                # Custom nickname - use default model
                machine_type = f"AxiDraw ({device_identifier})"
                machine_model = int(os.environ.get("AXIDRAW_MODEL", "4"))

            print(f"  Device nickname: {device_identifier}")
            print(f"  Detected machine type: {machine_type}")
            print(f"  Model number: {machine_model}")

        status_data["machine"] = machine_type
        status_data["model_number"] = machine_model

        # Load configuration for the detected model
        config_env_key = f"AXIDRAW_MODEL_{machine_model}_CONFIG"
        config_path = os.environ.get(config_env_key)

        if config_path:
            print(f"  Loading config from: {config_path}")
            config_data = load_axidraw_config(config_path)

            # Select appropriate travel dimensions based on machine model
            if machine_model == 1:  # AxiDraw V2/V3
                config_data['x_travel'] = config_data.get('travel_dimensions', {}).get('x_travel_default')
                config_data['y_travel'] = config_data.get('travel_dimensions', {}).get('y_travel_default')
            elif machine_model == 2:  # AxiDraw V3/A3 or SE/A3
                config_data['x_travel'] = config_data.get('travel_dimensions', {}).get('x_travel_V3A3')
                config_data['y_travel'] = config_data.get('travel_dimensions', {}).get('y_travel_V3A3')
            elif machine_model == 3:  # AxiDraw V3 XLX
                config_data['x_travel'] = config_data.get('travel_dimensions', {}).get('x_travel_V3XLX')
                config_data['y_travel'] = config_data.get('travel_dimensions', {}).get('y_travel_V3XLX')
            elif machine_model == 4:  # AxiDraw MiniKit
                config_data['x_travel'] = config_data.get('travel_dimensions', {}).get('x_travel_MiniKit')
                config_data['y_travel'] = config_data.get('travel_dimensions', {}).get('y_travel_MiniKit')
            elif machine_model == 5:  # AxiDraw SE/A1
                config_data['x_travel'] = config_data.get('travel_dimensions', {}).get('x_travel_SEA1')
                config_data['y_travel'] = config_data.get('travel_dimensions', {}).get('y_travel_SEA1')
            elif machine_model == 6:  # AxiDraw SE/A2
                config_data['x_travel'] = config_data.get('travel_dimensions', {}).get('x_travel_SEA2')
                config_data['y_travel'] = config_data.get('travel_dimensions', {}).get('y_travel_SEA2')

            # Remove the full travel_dimensions object, keep only the specific machine's values
            if 'travel_dimensions' in config_data:
                del config_data['travel_dimensions']

            status_data["config"] = config_data
            status_data["config"]["config_file"] = config_path
        else:
            print(f"  No config file found for model {machine_model} (env var: {config_env_key})")
            status_data["config"]["config_file"] = None

        # Check Power Status
        ad.interactive()
        if ad.connect():

            # Query current, voltage
            try:
                raw_string = ad.usb_query('QC\r')

                # Parse
                split_string = raw_string.split(",", 1)
                voltage_value = int(split_string[1])
                if voltage_value >= 250:
                    status_data["status"] = "on"
                else:
                    status_data["status"] = "connected"  # USB connected but powered off

                # Add voltage information to status
                status_data["voltage"] = voltage_value

            except (ValueError, IndexError):
                # If we can't parse voltage, at least we know it's connected
                status_data["status"] = "connected"

            # Disable xy
            ad.options.mode = "manual"
            ad.options.manual_cmd = "disable_xy"
            ad.plot_run()

            ad.disconnect()

    return status_data

@app.route('/status')
def status():
    """Original status endpoint - returns plain text for backwards compatibility"""
    status_data = get_plotter_status()

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
    import json
    status_data = get_plotter_status()

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