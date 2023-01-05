#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Scanner Web Server - Website to talk to scanimage

"""Scanner Web Server - Website to talk to scanimage
Copyright (C) 2022  CoolCat467

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>."""

__title__ = 'Scanner Web Server'
__author__ = 'CoolCat467'
__version__ = '1.0.1'

from typing import Any, Final, Optional, Union

from os import path
import sys
import subprocess
import socket
from configparser import ConfigParser
from pathlib import Path
from urllib.parse import urlencode
import time

import trio
from hypercorn.config import Config
from hypercorn.trio import serve
from quart_trio import QuartTrio
from quart import request, Response
from werkzeug import Response as wkresp


def log(message: str, level: int = 0) -> None:
    "Log a message to console and log file."
    levels = ['INFO', 'ERROR']
    
    root_dir = path.split(__file__)[0]
    log_file = path.join(root_dir, 'log.txt')
    
    log_level = levels[min(max(0, level), len(levels)-1)]
    log_time = time.asctime()
    log_message_text = message.encode("unicode_escape").decode("utf-8")
    
    log_msg = f'[{__title__}] [{log_time}] [{log_level}] {log_message_text}'
    
    if not path.exists(log_file):
        with open(log_file, mode='w', encoding='utf-8') as file:
            file.close()
        log('Log file does not exist!', 1)
        log('Created log file')
    with open(log_file, mode='a', encoding='utf-8') as file:
        file.write(f'{log_msg}\n')
        file.close()
    print(log_msg)


def call_command(command: tuple[str, ...],
                 get_output: bool=True) -> str:
    "Calls a given command in a sub-shell and returns the output as a string"
    command_msg = ' '.join(f"'{x}'" for x in command)
    log(f'Running system command "{command_msg}"')
    if get_output:
        try:
            with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
                # Call the process and pipe results back to us when done
                output = process.communicate()[0]
        except FileNotFoundError:
            # If the command does not exist, return nothing
            return ''
        try:
            return output.decode('utf-8')
        except TypeError:
            # Shouldn't happen, but still.
            return str(output)
    try:
        with subprocess.Popen(command) as process:
            # Call the process
            process.communicate()
    except FileNotFoundError:
        # If the command does not exist, return nothing
        pass
    return ''


def find_ip() -> str:
    "Utility function to guess the IP where the server can be found from the network"
    # we get a UDP-socket for the TEST-networks reserved by IANA.
    # It is highly unlikely, that there is special routing used
    # for these networks, hence the socket later should give us
    # the IP address of the default route.
    # We're doing multiple tests, to guard against the computer being
    # part of a test installation.
    
    candidates = []
    for test_ip in ('192.0.2.0', '198.51.100.0', '203.0.113.0'):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect((test_ip, 80))
        ip_addr = sock.getsockname()[0]
        sock.close()
        if ip_addr in candidates:
            return ip_addr
        candidates.append(ip_addr)
    
    return candidates[0]


def indent(level: int, text: str) -> str:
    "Indent text by level of spaces."
    return '\n'.join(' '*level+line for line in text.splitlines())


def deindent(level: int, text: str) -> str:
    "Undo indent on text by level of characters."
    return '\n'.join(line[level:] for line in text.splitlines())


def get_scanners() -> dict[str, str]:
    "Get scanner scanners"
    printers = call_command(('scanimage', '-f', '%m=%d\n'))
    if printers == '':
        return {}
    device_map = []
    for printer in printers.replace('\\n', '\n').splitlines():
        device_map.append(printer.split('='))
    return dict(device_map)


def part_quotes(text: str, which: int, quotes: str="'") -> str:
    "Return part which of text within quotes."
    return text.split(quotes)[which*2+1]


class DeviceSetting:
    "Setting for device"
    __slots__ = ('name', 'options', 'default', 'desc', 'set')
    def __init__(self,
                 name: str,
                 options: list[str],
                 default: str,
                 desc: str) -> None:
        self.name = name
        self.options = options
        self.default = default
        self.desc = desc
        self.set = self.default
    
    def as_argument(self) -> str:
        "Return setting as argument"
        return f'--{self.name}={self.set}'
    
    def __repr__(self) -> str:
        return f'DeviceSetting({self.name!r}, {self.options!r}, {self.default!r}, {self.desc!r})'


app: Final = QuartTrio(__name__)  # pylint: disable=invalid-name
app_storage: Final[dict[str, Any]] = {}  # pylint: disable=invalid-name


def get_device_settings(device: str) -> list[DeviceSetting]:
    "Get device settings. Cache results in app storage."
    # Cache response
    if device in app_storage['device_settings']:
        return app_storage['device_settings'][device]
    
    response = call_command(('scanimage', f'--device-name={device}',
                             '--format=pnm', '--dont-scan', '--all-options'))
    
    if response == '':
        app_storage['device_settings'][device] = []
        return []
    
    lines = response.strip().splitlines()
    
    if device != part_quotes(lines[0].replace('`', "'"), 0):
        raise RuntimeError('Device from scanimage does not match requested!')
    
    settings = []
    found = False
    for line in deindent(2, '\n'.join(lines[1:])).splitlines():
        if line == 'Scan mode:':
            found = True
            continue
        if line[0] != ' ':
            found = False
            continue
        if found:
            settings.append(deindent(2, line).replace('||', '|'))
    options: dict[str, list] = {}
    cur_option = ''
    for line in settings:
        if line.startswith('--'):
            line = deindent(2, line).replace('[', ' ').replace(']', ' ').replace('  ', ' ')
            line = line.replace('=', ' ').replace('  ', ' ').replace('(', ' ').replace(')', ' ')
            line = line.replace('  ', ' ').strip()
            name, possible, default = line.split(' ')
            cur_option = name
            if name == 'resolution':
                possible = possible[:-3]# Remove <text>dpi
            options[name] = [possible.split('|'), default, []]
        else:
            options[cur_option][2].append(deindent(4, line))
    for name in list(options.keys()):
        options[name][2] = ' '.join(options[name][2])
        if len(options[name][0]) <= 1 or 'button' in name:
            del options[name]
    
    device_settings = []
    for name, values in options.items():
        device_settings.append(DeviceSetting(name, *values))
    
    # Cache
    app_storage['device_settings'][device] = device_settings
    return device_settings


def preform_scan(device_name: str, out_type: str='png') -> str:
    "Scan using device and return path."
    if not out_type in {'pnm', 'tiff', 'png', 'jpeg'}:
        raise ValueError('Output type must be pnm, tiff, png, or jpeg')
    filename = 'scan.'+out_type
    filepath = path.join(str(app.static_folder), filename)
    
    settings = []
    for setting in app_storage['device_settings'][device_name]:
        if setting.set != setting.default:
            settings.append(setting.as_argument())
    
    command = ('scanimage', f'--device-name={device_name}',
               f'--format={out_type}', f'--output-file={filepath}')+tuple(settings)
    call_command(command, False)
    return filename


def get_tag(tag_type: str, args: Optional[dict[str, str]] = None) -> str:
    "Get HTML tag"
    tag_args = ''
    if args is not None:
        tag_args = ' '+' '.join(f'{k}="{v}"' for k, v in args.items())
    return f'<{tag_type}{tag_args}>'


def wrap_tag(tag_type: str,
             value: str,
             is_block: bool = True,
             tag_args: Optional[dict[str, str]] = None) -> str:
    "Wrap value in HTML tag"
    if is_block:
        value = f'\n{indent(2, value)}\n'
    start_tag = get_tag(tag_type, tag_args)
    return f'{start_tag}{value}</{tag_type}>'


def get_template(page_name: str, body: str='') -> str:
    "Get template for page"
    return f"""<!DOCTYPE HTML>
<html lang=en>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{page_name}</title>
    <style>
      * {{
        font-family: "Lucida Console";
      }}
      h1, footer {{
        text-align: center;
      }}
      footer {{
        position: absolute;
        bottom: 0;
        width: 100%;
      }}
    </style>
  </head>
  <body>
    <h1>{page_name}</h1>
{indent(4, body)}
    <footer>
      <hr>
      <p>{__title__} v{__version__} © {__author__}</p>
    </footer>
  </body>
</html>"""


def contain_in_box(inside: str, name: Optional[str] = None) -> str:
    "Contain HTML in a box."
    if name is not None:
        inside = f'<span>{name}</span>\n<br>\n'+inside
    return f"""
<div style="background: ghostwhite;
            padding: 4px;
            border: 1px solid lightgray;
            margin: 4px;">
{indent(2, inside)}
</div>"""[1:]


def radio_select_dict(submit_name: str,
                      options: dict[str, str],
                      default: Optional[str] = None) -> str:
    "Create radio select from dictionary"
    lines = []
    count = 0
    for display, value in options.items():
        cid = f'{submit_name}_{count}'
        args = {
            'type': 'radio',
            'id': cid,
            'name': submit_name,
            'value': value
        }
        if value == default:
            args['checked'] = 'checked'
        lines.append(get_tag('input', args))
        lines.append(wrap_tag('label', display, False, {'for': cid}))
        lines.append('<br>')
        count += 1
    return '\n'.join(lines)


def radio_select_box(submit_name: str,
                     options: dict[str, str],
                     default: Optional[str] = None,
                     box_title: Optional[str] = None) -> str:
    "Create radio select value box from dictionary and optional names"
    radios = radio_select_dict(submit_name, options, default)
    return contain_in_box('<br>\n'+radios, box_title)


def get_list(values: list[str]) -> str:
    "Return HTML list from values"
    display = '\n'.join(wrap_tag('li', v) for v in values)
    return wrap_tag('ul', display)


def get_form(name: str,
             contents: str,
             submit_display: str,
             form_title: Optional[str] = None) -> str:
    "Return HTML form"
    submit = get_tag('input', {
        'type': 'submit',
        'value': submit_display
    })
    html = f"""{contents}
<br>
{submit}"""
    title = ''
    if form_title is not None:
        title = f'<b>{form_title}</b>\n'
    return title+wrap_tag('form', html, True, {
        'name': name,
        'method': 'post'
    })


@app.get('/')
async def root_get() -> str:
    "Main page get request"
    options = {'None - <a href="/update_scanners">Update Scanners</a>': 'none'}
    default = 'none'
    
    if app_storage['scanners']:
        options = {k:k for k in app_storage['scanners']}
        # Since radio_select_dict is if comparison for
        # default, if default device does not exist
        # there simply won't be a default shown.
        default = app_storage['default_device']
    
    scanner_select = radio_select_box(
        'scanner',
        options,
        default,
        'Select a Scanner:'
    )
    
    image_format = radio_select_box(
        'img_format',
        {v.upper():v for v in ('png', 'jpeg', 'pnm', 'tiff')},
        'png',
        'Select Image format:'
    )
    
    form_content = '\n'.join((image_format, scanner_select))
    
    form = get_form('scan_request',
                    form_content,
                    'Scan!',
                    'Press Scan to start scanning.')
    
    html = f"""{form}
<hr>
<a href="/update_scanners"><button>Update Scanners</button></a>
<a href="/scanners"><button>Scanner Settings</button></a>"""
    
    return get_template('Request Scan', html)


@app.post('/')
async def root_post() -> Union[Response, wkresp]:
    "Main page post handling"
    multi_dict = await request.form
    data = multi_dict.to_dict()
    
    # Validate input
    img_format = data.get('img_format', 'png')
    device = app_storage['scanners'].get(data.get('scanner', 'none'), 'none')
    
    if img_format not in {'pnm', 'tiff', 'png', 'jpeg'}:
        return app.redirect('/')
    if device == 'none':
        return app.redirect('/scanners')
    
    filename = preform_scan(device, img_format)
    
    return await app.send_static_file(filename)


@app.get('/update_scanners')
async def update_scanners_get() -> wkresp:
    "Update scanners get handling"
    app_storage['scanners'] = get_scanners()
    for device in app_storage['scanners'].values():
        get_device_settings(device)
    return app.redirect('scanners')


@app.get('/scanners')
async def scanners_get() -> str:
    "Scanners page get handling"
    scanners = '<p>There were no scanners detected. Are your printer(s) turned on?</p>'
    
    if app_storage['scanners']:
        scanners_list = []
        for disp in app_storage['scanners']:
            scanner_url = urlencode({'scanner': disp})
            link = f'settings?{scanner_url}'
            scanners_list.append(wrap_tag('a', disp, False, {'href': link}))
        scanners = get_list(scanners_list)
    
    scanners = contain_in_box(scanners, 'Scanners:')
    return get_template('Scanners', f"""{scanners}
<br>
<a href="/update_scanners"><button>Update Scanners</button></a>
<a href="/"><button>Scan Request</button></a>""")


def get_setting_radio(setting: DeviceSetting) -> str:
    "Return setting radio section"
    name = setting.name.replace('-', ' ').title()
    return radio_select_box(
        setting.name,
        {x.title():x for x in setting.options},
        setting.set,
        f'{name} - {setting.desc}'
    )


@app.get('/settings')
async def settings_get() -> Union[wkresp, str]:
    "Settings page get handling"
    scanner = request.args.get('scanner', 'none')
    
    if scanner == 'none' or scanner not in app_storage['scanners']:
        return app.redirect('/scanners')
    
    device = app_storage['scanners'][scanner]
    scanner_settings = app_storage['device_settings'][device]
    
    radios = []
    for setting in scanner_settings:
        radios.append(get_setting_radio(setting))
    
    contents = '<p>There are no additional settings for this scanner.</p>'
    if radios:
        contents = get_form('settings_update',
                            '\n'.join(radios),
                            'Save',
                            f'Settings for "{scanner}":')
    
    html = f"""{contents}
<hr>
<a href="/"><button>Scan Request</button></a>
<a href="/scanners"><button>Scanner Settings</button></a>"""
    return get_template(scanner, html)


@app.post('/settings')
async def settings_post() -> Union[wkresp, str]:
    "Settings page post handling"
    scanner = request.args.get('scanner', 'none')
    
    if scanner == 'none' or scanner not in app_storage['scanners']:
        return app.redirect('/scanners')
    
    device = app_storage['scanners'][scanner]
    scanner_settings = app_storage['device_settings'][device]
    
    valid_settings = {setting.name: idx for idx, setting in enumerate(scanner_settings)}
    
    multi_dict = await request.form
    data = multi_dict.to_dict()
    
    for setting_name, new_value in data.items():
        # Input validation
        if not setting_name in valid_settings:
            continue
        idx = valid_settings[setting_name]
        if not new_value in scanner_settings[idx].options:
            continue
        app_storage['device_settings'][device][idx].set = new_value
    
    # Return to page for that scanner
    return app.redirect(request.url)


async def serve_scanner(root_dir: str,
                        device_name: str,
                        port: int=3004,
                        ip_addr: str='') -> None:
    "Server scanner"
    
    if not ip_addr:
        ip_addr = find_ip()
        
    try:
        # Add more information about the address
        location = f'{ip_addr}:{port}'
        
        config = {
            'bind': location,
            'worker_class': 'trio',
            'errorlog': path.join(root_dir, 'log.txt'),
        }
        app.static_folder = Path(root_dir)
        app_storage['scanners'] = {}
        app_storage['default_device'] = device_name
        app_storage['device_settings'] = {}
        
        config_obj = Config.from_mapping(config)
        
        print(f'Serving on http://{location}\n(CTRL + C to quit)')
        
        await serve(app, config_obj)
    except socket.error:
        log(f"Cannot bind to IP address '{ip_addr}' port {port}", 1)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


def run() -> None:
    "Run scanner server"
    root_dir = path.split(__file__)[0]
    
    config = ConfigParser()
    conf_file = path.join(root_dir, 'config.txt')
    config.read(conf_file)
    
    target = 'None'
    port = 3004
    
    rewrite = True
    if config.has_section('main'):
        rewrite = False
        if config.has_option('main', 'printer'):
            target = config.get('main', 'printer')
        else:
            rewrite = True
        if config.has_option('main', 'port'):
            raw = config.get('main', 'port')
            rewrite = True
            if raw.isdigit():
                port = int(raw)
                rewrite = False
        else:
            rewrite = True
    
    if rewrite:
        config.clear()
        config.read_dict({'main': {'target': target, 'port': port}})
        with open(conf_file, 'w', encoding='utf-8') as config_file:
            config.write(config_file)
    
    print(f'Default Printer: {target}\nPort: {port}\n')
    
    if target == 'None':
        print("No default device in config file. Select one from `scanimage -L`.")
    
    trio.run(serve_scanner, root_dir, target, port)


if __name__ == '__main__':
    print(f'{__title__} v{__version__}  Copyright (C) 2022  {__author__}\n')
    run()
