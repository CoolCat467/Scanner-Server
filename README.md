# Scanner-Server
Kind of like CUPS but it's for scanning, not printing.

![thumbnail](./img/thumbnail.png)

This is basically a web interface for the `scanimage` Linux command-line tool which talks
to devices through the SANE (Scanner Access Now Easy) interface.

This program is intended to be used alongside CUPS, but may not be required.

The web server is hosted on port `3004` when running.

**Warning: This program runs commands on your system to talk to `scanimage`.**

## Installation
```console
pip install -r requirements.txt
```

## Run
```console
python3 scanner_server.py 
```

## Usage
Go to URL `http://<IP_of_host>:3004`
