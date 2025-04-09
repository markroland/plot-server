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

# Load settings from environment
load_dotenv()

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

    # Get files
    plot_files = os.listdir(os.environ.get("ART_DIRECTORY"));

    return render_template('index.html', files=plot_files)

def plot(filepath, layer=0):
    # Load file & configure plot context
    ad.plot_setup(filepath)
    ad.options.mode = "plot"

    # Set configuration (Not working)
    # config_path = os.environ.get("AXIDRAW_CONFIG")
    # ad.load_config(config_path)
    # if os.path.exists(config_path):
    #     ad.load_config(config_path)
    # else:
    #     print(f"Configuration File Not Found: {config_path}")

    if layer > 0:
        ad.options.mode = "layers"
        ad.options.layer = layer

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

@app.route('/status')
def status():

    # Default to "Off"
    status_text = "off"

    # See https://axidraw.com/doc/py_api/#list_names
    # Note: This indicates USB connection and NOT power on
    ad.plot_setup()
    ad.options.mode = "manual"
    ad.options.manual_cmd = "list_names"
    out = ad.plot_run()
    axidraw_list = ad.name_list

    #print(type(axidraw_list))
    if axidraw_list is not None:

        # For future: Print device names
        # for x in range(len(axidraw_list)):
            # print(axidraw_list[x])

        # Check Power
        # Open serial port to AxiDraw;
        ad.interactive()
        if ad.connect():

            # Query current, voltage
            raw_string = ad.usb_query('QC\r')

            # Disable xy
            ad.options.mode = "manual"
            ad.options.manual_cmd = "disable_xy"
            ad.plot_run()

            ad.disconnect()

            # Parse
            split_string = raw_string.split(",", 1)
            voltage_value = int(split_string[1])
            if voltage_value >= 250:
                status_text = "on"

    response = Response(status_text, mimetype='text/plain')

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