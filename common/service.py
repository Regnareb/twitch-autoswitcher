# coding: utf-8
import re
import copy
import time
import socket
import urllib
import inspect
import logging
import requests
import webbrowser

from requests_oauthlib import OAuth2Session
from oauthlib.oauth2.rfc6749.errors import InvalidGrantError, MissingTokenError, InvalidClientError, InvalidTokenError, InvalidClientIdError

import common.manager
import common.tools as tools
logger = logging.getLogger(__name__)


class Timeout(Exception):
    pass


class Service():
    def __init__(self, config):
        self.infos = {'online': '', 'title': '', 'name': '', 'category': '', 'description': ''}
        self.manager = common.manager.ManageStream()
        if not config:
            self.config = self.default_config()
        else:
            self.config = config
        self.oauth2 = OAuth2Session(token=self.config['authorization'], client_id=self.config['client_id'], scope=self.config['scope'], redirect_uri=self.config['redirect_uri'])
        self.get_token()
        self.get_channel_id()

    @classmethod
    def default_config(cls):
        return {
            "enabled": False,
            "scope": cls.scope,
            "authorization_base_url": cls.authorization_base_url,
            "token_url": cls.token_url,
            "redirect_uri": cls.redirect_uri,
            "authorization": {},
            "client_id": '',
            "client_secret": ''
        }

    def set_headers(self):
        self.headers = {
            'Client-ID': self.config['client_id'],
            'Authorization': 'OAuth ' + self.config['authorization']['access_token']
         }
        self.headers2 = {
            'Client-ID': self.config['client_id'],
            'Authorization': 'Bearer ' + self.config['authorization']['access_token']
         }

    def token_isexpired(self):
        return time.time() > self.config['authorization']['expires_at']

    def get_token(self):
        try:
            if self.token_isexpired():
                self.refresh_token()
        except (KeyError, Warning, InvalidGrantError, MissingTokenError, InvalidClientIdError):
            logger.info('Asking an access code for {}'.format(self.name))
            port = re.search(r':(\d*)/?$', self.config['redirect_uri'])
            port = int(port.group(1))
            authorization_url, _ = self.oauth2.authorization_url(self.config['authorization_base_url'], state=self.config['client_secret'], access_type='offline')
            serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            serversocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            serversocket.bind(('localhost', port))
            serversocket.listen(5)
            webbrowser.open(authorization_url)
            currenttime = time.time()
            while True:
                connection, _ = serversocket.accept()
                buf = connection.recv(4096)
                if buf:
                    break
                if time.time() - currenttime > self.manager.config['base']['timeout']:
                    raise Timeout()
            code = re.search('code=(.*?)&', str(buf))
            code = code.group(1)
            code = urllib.parse.unquote(code)
            logger.debug('The code is {}. Asking for the authorization token'.format(code))
            self.config['authorization'] = self.oauth2.fetch_token(self.config['token_url'], code, include_client_id=True, client_secret=self.config['client_secret'])
        self.set_headers()

    def refresh_token(self):
        try:
            self.config['authorization'] = self.oauth2.refresh_token(self.config['token_url'], **{'client_id': self.config['client_id'], 'client_secret': self.config['client_secret']})
        except (InvalidGrantError, MissingTokenError, InvalidClientIdError):
            logger.warning("Couldn't refresh the token")
            raise

    def query_category(self, category):
        return {}

    def validate_category(self, category):
        return True

    def update_channel(self, infos):
        self.get_token()
        infos = copy.deepcopy(infos)
        infos['name'] = self.name
        infos = tools.parse_strings(infos)
        infos['category'] = self.manager.config.get('assignations', {}).get(infos['category'], {}).get(self.name, {}).get('name', '')
        return infos

    def request(self, action, address, headers=None, data=None, params=None):
        if not headers:
            headers = self.headers
        action = getattr(requests, action)
        response = action(address, headers=headers, json=data, params=params)
        curframe = inspect.currentframe()
        outframe = inspect.getouterframes(curframe, 2)[1][3]
        self.log_requests(outframe, address, response)
        return response

    def log_requests(self, action, address, response):
        try:
            if response.status_code == 401:
                logger.warning('Error 401 for service {}, requesting another OAuth token'.format(self.name))
                self.get_token()
            elif not response:
                logger.error('{} - {}: {} {}'.format(self.name, action, address, response.json()))
            else:
                logger.debug(response.json())
        except:
            logger.info(response)  # Some reponse return an empty JSON
