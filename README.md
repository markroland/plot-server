# Plot Server

## Installation

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
