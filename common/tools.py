import re
import os
import sys
import glob
import json
import shutil
import ctypes
import logging
import tempfile
import importlib
import functools
import threading
import subprocess
from io import BytesIO
from zipfile import ZipFile

import psutil
import requests
from contextlib import contextmanager
logger = logging.getLogger(__name__)

@contextmanager
def pause_services(services):
    if sys.platform=='win32':
        admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        if admin:
            for service in services:
                subprocess.Popen('net stop "{}"'.format(service), creationflags=subprocess.CREATE_NO_WINDOW)
        elif services:
            logger.warning("No administrator rights, can't pause Windows Services")
        yield
        if admin:
            for service in services:
                subprocess.Popen('net start "{}"'.format(service), creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        yield

@contextmanager
def pause_processes(processes):
    if sys.platform in ['Windows', 'win32', 'cygwin']:
        for process in processes:
            subprocess.Popen('lib/pssuspend.exe "{}"'.format(process), creationflags=subprocess.CREATE_NO_WINDOW, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        yield
        for process in processes:
            subprocess.Popen('lib/pssuspend.exe -r "{}"'.format(process), creationflags=subprocess.CREATE_NO_WINDOW, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    else:
        for process in processes:
           subprocess.Popen('pkill -TSTP "{}$"'.format(process), shell=True)
        yield
        for process in processes:
            subprocess.Popen('pkill -CONT "{}$"'.format(process), shell=True)

def download_pssuspend(path):
    url = 'https://download.sysinternals.com/files/PSTools.zip'
    response = requests.get(url)
    zipfile = ZipFile(BytesIO(response.content))
    pssuspend = zipfile.extract('pssuspend.exe', path)
    pssuspend = zipfile.extract('pssuspend64.exe', path)
    return pssuspend

def threaded(func):
    @functools.wraps(func)
    def async_func(*args, **kwargs):
        func_hl = threading.Thread(target=func, args=args, kwargs=kwargs)
        func_hl.start()
        return func_hl
    return async_func

def loadmodules(path, subfolder):
    modules = glob.glob(os.path.join(path, subfolder, '*.py'))
    modules = ['.'.join([subfolder, os.path.basename(i)[:-3]]) for i in modules]
    data = {}
    for module in modules:
        data[module.split('.')[-1]] = importlib.import_module(module)
    return data

def getForegroundProcess():
    if sys.platform in ['Windows', 'win32', 'cygwin']:
        user32 = ctypes.windll.user32
        h_wnd = user32.GetForegroundWindow()
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(h_wnd, ctypes.byref(pid))
        return psutil.Process(pid.value).exe().replace('\\', '/')
    elif sys.platform in ['Mac', 'darwin', 'os2', 'os2emx']:
        import AppKit
        return str(AppKit.NSWorkspace.sharedWorkspace().activeApplication()['NSApplicationPath'])
        process = os.path.basename(str(AppKit.NSWorkspace.sharedWorkspace().activeApplication()['NSApplicationPath']))
    else:
        process = ''
    return process

def listservices(namefilter='', status=''):
    if sys.platform != 'win32':
        return {}
    services = {}
    for i in psutil.win_service_iter():
        if namefilter and namefilter.lower() not in i.name().lower() or status and i.status() != status:
            continue
        services[i.binpath()] = i.as_dict()
    return services

def listprocesses():
    result = {}
    ignorelist = ['System Idle Process', 'System', 'svchost.exe', 'csrss.exe', 'services.exe', 'conhost.exe', 'wininit.exe', 'lsass.exe', 'lsm.exe', 'winlogon.exe', 'rundll32.exe', 'taskkill.exe']
    for proc in psutil.process_iter():
        try:
            name = proc.name()
            exe = proc.exe()
            memory = proc.memory_percent()  # Fix an OSX bug returning None
            if name in ignorelist:
                continue
            if exe in result:
                result[exe]['memory_percent'] += memory
            else:
                result[exe] = proc.as_dict(attrs=['name', 'exe', 'nice', 'num_threads'])
                result[exe]['memory_percent'] = memory
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
        except FileNotFoundError:
            logger.error('Strange process: {}'.format(proc.name(), proc.pid))
    return result

def parse_strings(infos):
    for key in infos:
        try:
            infos[key] = infos[key].replace('%SERVICE%', infos.get('name', '%SERVICE%'))
            infos[key] = infos[key].replace('%CATEGORY%', infos.get('category', '%CATEGORY%'))
            infos[key] = infos[key].replace('%CUSTOMTEXT%', infos.get('customtext', '%CUSTOMTEXT%'))
        except AttributeError:
            pass
    return infos

def load_json(path, backup=True):
    content = {}
    try:
        with open(path) as json_file:
            content = json.load(json_file)
    except FileNotFoundError:
        return None
    except (json.decoder.JSONDecodeError, UnicodeDecodeError):
        if backup:
            shutil.copy(path, path + '_error')
        logger.error('There was an error in the json file, you can view it at this path: {}'.format(path+'_error'))
        return False
    return content

def save_json(data, path):
    with tempfile.NamedTemporaryFile('w', delete=False) as tmp:
        json.dump(data, tmp, indent=4)
    shutil.move(tmp.name, path)
    return True


class Borg:
    __shared_state = {}
    def __init__(self):
        super().__init__()
        self.__dict__ = self.__shared_state


class HtmlStreamHandler(logging.StreamHandler):
    CRITICAL = {'color': 'brown', 'size': '120%', 'special': 'font-weight:bold', 'after': '' }
    ERROR    = {'color': 'red', 'size': '100%', 'special': '', 'after': ''}
    WARNING  = {'color': 'darkorange', 'size': '100%', 'special': '', 'after': ''}
    INFO     = {'color': 'black', 'size': '100%', 'special': '', 'after': ''}
    DEFAULT  = {'color': 'black', 'size': '100%', 'special': '', 'after': ''}
    DEBUG    = {'color': 'grey', 'size': '100%', 'special': '', 'after': ''}

    def __init__(self, stream=None):
        super().__init__(stream=stream)

    @classmethod
    def _get_params(cls, level):
        if level >= logging.CRITICAL:return cls.CRITICAL
        elif level >= logging.ERROR:   return cls.ERROR
        elif level >= logging.WARNING: return cls.WARNING
        elif level >= logging.INFO:    return cls.INFO
        elif level >= logging.DEBUG:   return cls.DEBUG
        else:                          return cls.DEFAULT

    def format(self, record):
        regex = r"((?:\w):(?:\\|/)[^\s/$.?#].[^\s]*)"
        regex = re.compile(regex, re.MULTILINE)
        text = logging.StreamHandler.format(self, record)
        text = re.sub(regex, r'<a href="file:///\g<1>">\g<1></a>', text)
        params = self._get_params(record.levelno)
        return '<span class="{1}" style="color:{color};font-size:{size};{special}">{0}</span>{after}'.format(text, record.levelname.lower(), **params)
