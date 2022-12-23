# Plot Server

The purpose of this project is to provide a web-based interface for starting plots
with an AxiDraw plotter. It looks at a folder of SVG images, and provides controls
for viewing and plotting them.

## Installation

### Install dependencies

```
pip install flask
pip install python-dotenv
```

### Copy .env.example to .env

Customize your values

### Add a symoblic link for artwork

Run this command where `{ART_DIRECTORY}` is replaced with the value in your .env file:

```
cd ./static
ln -s {ART_DIRECTORY} sketches
```

### Install the AxiDraw Python module and CLI

See instructions on [AxiDraw.com](https://axidraw.com/doc/py_api).

## Run

```
python index.py
```

Run in the background:

```
python index.py &
```

Keep running in background if shell session ends:

```
nohup python index.py > /dev/null 2>&1 &
```

## Connect

Open the URL initiated by Flask in your web browser. It should be your local IP
address followed by the port, so something like `http://192.168.0.10:5007`.