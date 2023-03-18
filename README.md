# Scanner-Server
Kind of like CUPS but it's for scanning, not printing.

![thumbnail](./img/thumbnail.png)

This is basically a web interface for the `scanimage` Linux command-line tool which talks
to devices through the SANE (Scanner Access Now Easy) interface.

This program is intended to be used alongside CUPS, but may not be required.

The web server is hosted on port `3004` on default when running.
This can be changed in `config.ini`

You will need `libsane-dev` to be able to install `python-sane`.
Use APT or your system's equivalent to install.

## Installation
```console
sudo apt install libsane-dev
git clone https://github.com/CoolCat467/Scanner-Server.git
pip install sanescansrv
```

## Run
Important: When you run this program, the configuration file and the
logs folder will be saved in `~/.sanescansrv/` and the program
will create it if it does not exist.
```console
sanescansrv
```

## Usage
Go to URL `http://<IP_of_host>:3004`
