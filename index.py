#!/usr/bin/env python3
#
# AxiDraw Plot Server
#
# Run in background even after a hang up:
#  nohup python index.py > /dev/null 2>&1 &

from dotenv import load_dotenv
import threading
from pyaxidraw import axidraw
from flask import Flask, request, render_template
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

# Define route: Default
@app.route('/')
def index():

    # Get files
    plot_files = os.listdir(os.environ.get("ART_DIRECTORY"));

    return render_template('index.html', files=plot_files)

# Define route for a plot request
@app.route('/plot/<file>', methods=['GET'])
def plot(file):

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

            # Load file & configure plot context
            ad.plot_setup(filepath)
            ad.options.mode = "plot"

            if layer > 0:
                ad.options.mode = "layers"
                ad.options.layer = layer

            # Plot the file
            ad.plot_run()

            # Turn off motors
            ad.options.mode = "manual"
            ad.options.manual_cmd = "disable_xy"
            ad.plot_run()

            # Release the Semaphore
            sem.release()

            response = 'Done: ' + str(layer)

        else:

            response = 'Busy', 503

    return response

@app.route('/status')
def status():

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
                return "on"

    return "off"

# Set up cross origin resource sharing
# @app.after_request
# def after_request(response):
#     response.headers.add('Access-Control-Allow-Origin', 'http://project.markroland.com')
#     response.headers.add('Access-Control-Allow-Methods', 'GET, OPTIONS, POST, PUT')
#     return response

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=os.environ.get("HOST_PORT"))