#!/usr/bin/env python3
#
# AxiDraw Plot Server
#
# Run in background even after a hang up:
#  nohup python index.py > /dev/null 2>&1 &

from flask import Flask, request, render_template
from dotenv import load_dotenv
import os

# Load settings from environment
load_dotenv()

# Create new Flask app
app = Flask(__name__)

# Define route: Default
@app.route('/')
def index():

    # Get files
    plot_files = os.listdir(os.environ.get("ART_DIRECTORY"));

    return render_template('index.html', files=plot_files)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=os.environ.get("HOST_PORT"))