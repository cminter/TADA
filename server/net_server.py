#!/bin/env python3
import logging
import sys
import os
import traceback
import threading
import socketserver
import json
import datetime
from dataclasses import dataclass, field
from typing import ClassVar
import enum

import net_common as nc
import util

K = nc.K
Mode = nc.Mode

server_id = None
server_key = None
server_protocol = None
server_lock = threading.Lock()


class Error(str, enum.Enum):
    server1 = 'server1'
    server2 = 'server2'
    user_id = 'user_id'
    login1 = 'login1'
    login2 = 'login2'
    multiple = 'multiple'


@dataclass
class Message(object):
    lines: list
    mode: Mode = Mode.app
    changes: dict = field(default_factory=lambda: {})
    choices: dict = field(default_factory=lambda: {})
    prompt: str = ''
    error: str = ''
    error_line: str = ''


connected_users = set()


@dataclass
class LoginHistory(object):
    addr: str
    no_user_attempts: dict = field(default_factory=lambda: {})
    bad_password_attempts: dict = field(default_factory=lambda: {})
    fail_count: int = 0
    ban_count: int = 0

    _fail_limit: ClassVar[int] = 10

    def banned(self, update, save=False):
        is_banned = self.fail_count >= LoginHistory._fail_limit
        if is_banned and update:
            self.ban_count += 1
            if save:
                self.save()
        return is_banned

    def no_user(self, user_id, save=False):
        self.fail_count += 1
        attempts = self.no_user_attempts.get(user_id, 0)
        self.no_user_attempts[user_id] = attempts + 1
        if save:
            self.save()
        return self.banned(True, save=save)

    def fail_password(self, user_id, save=False):
        self.fail_count += 1
        attempts = self.bad_password_attempts.get(user_id, 0)
        self.bad_password_attempts[user_id] = attempts + 1
        if save:
            self.save()
        return self.banned(True, save=save)

    def succeed_user(self, user_id, save=False):
        self.fail_count = 0
        if user_id in self.bad_password_attempts:
            self.bad_password_attempts.pop(user_id)
        if save:
            self.save()

    @staticmethod
    def _json_path(addr):
        util.makeDirs(nc.net_dir)
        return os.path.join(nc.net_dir, f"client-{addr}.json")

    @staticmethod
    def load(addr):
        path = LoginHistory._json_path(addr)
        if os.path.exists(path):
            with open(path) as jsonF:
                lh_data = json.load(jsonF)
            return LoginHistory(**lh_data)
        else:
            return LoginHistory(addr)

    def save(self):
        with open(LoginHistory._json_path(self.addr), 'w') as jsonF:
            json.dump(self, jsonF, default=lambda o: {k: v for k, v
                                                      in o.__dict__.items() if v}, indent=4)


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    pass


class UserHandler(socketserver.BaseRequestHandler):
    def handle(self):
        addr = self.client_address[0]
        self.login_history = LoginHistory.load(addr)
        if self.login_history.banned(True, save=True):
            logging.warning("UserHandler.handle: ignoring banned IP %s" % addr)
            return
        port = self.client_address[1]
        self.sender = f"{addr}:{port}"
        self.ready = None
        self.user = None
        logging.info("UserHandler: handle: connect (addr=%s)" % self.sender)
        running = True
        while running:
            try:
                request = self._receive_data()
                if request is None:
                    running = False
                    break
                try:
                    if self.ready is None:  # assume init message
                        response = self._process_init(request)
                    elif self.user is None:
                        response = self._process_login(request)
                    else:
                        response = self.process_message(request)
                except Exception as e:
                    traceback.print_exc(file=sys.stdout)
                    # TODO: log error with message, error code to client
                    self._send_data(Message(lines=["Terminating session."],
                                            error_line=f"server side error ({e})",
                                            error=Error.server1, mode=Mode.bye))
                if response is None:
                    running = False
                else:
                    self._send_data(response)
            except Exception as e:
                traceback.print_exc(file=sys.stdout)
                # TODO: log error with message, error code to client
                self._send_data(Message(lines=["Terminating session."],
                                       error_line=f"server side error ({e})",
                                       error=Error.server2, mode=Mode.bye))
        if self.user is not None:
            user_id = self.user.id
            with server_lock:
                connected_users.remove(user_id)
        else:
            user_id = '?'
        logging.info("user_handler: disconnect %s (addr=%s)" % (user_id, self.sender))

    def _receive_data(self):
        return nc.fromJSONB(self.request.recv(1024))

    def _send_data(self, data):
        self.request.sendall(nc.toJSONB(data))

    def _process_init(self, data):
        client_id = data.get('id')
        if client_id == server_id:
            client_key = data.get('key')
            if client_key == server_key:
                # TODO: handle protocol difference
                self.ready = True
                return Message(lines=self.init_success_lines(), mode=Mode.login)
            else:
                # TODO: record history in case want to ban
                return None  # poser, ignore them
        else:
            # TODO: record history in case want to ban
            return None  # poser, ignore them

    def _process_login(self, data):
        user_id, password, invite_code = data['login']
        if user_id == '':
            return Message(lines=['User id required.'],
                           error_line='No user id.',
                           error=Error.user_id, mode=Mode.bye)

        def error_ban():
            return Message(lines=[],
                           error_line='Too many failed attempts.',
                           error=Error.login2, mode=Mode.bye)

        def error_login_failed():
            return Message(lines=self.login_fail_lines(),
                           error_line='Login failed.',
                           error=Error.login1, mode=Mode.login)

        user = nc.User.load(user_id)
        if user is None:
            invite = nc.Invite.load(user_id)
            if invite is None:
                logging.warning("process_login: login failed: no user '%s`" % user_id)
                # when failing don't tell that have wrong user id
                banned = self.login_history.no_user(user_id, save=True)
                if banned:
                    logging.info(f"ban {self.sender}")
                    return error_ban()
                return error_login_failed()
            else:
                # process new user with invite
                if invite.code != invite_code:
                    logging.warning(f"process_login: invalid invite code %s" % invite_code)
                    banned = self.login_history.no_user(user_id, save=True)
                    if banned:
                        logging.info("process_login: ban %s" % self.sender)
                        return error_ban()
                    else:
                        return error_login_failed()
                else:
                    # create and save user
                    user = nc.User(user_id)
                    user.hash_password(password)
                    user.save()
                    invite.delete()
        with server_lock:
            if user_id in connected_users:
                return Message(lines=['One connection allowed at a time.'],
                               error_line='Multiple connections.',
                               error=Error.multiple, mode=Mode.bye)
        if not user.matchPassword(password):
            logging.warning(f"bad password for '{user_id}'")
            banned = self.login_history.fail_password(user_id, save=True)
            if banned:
                logging.info(f"ban {self.sender}")
                return error_ban()
            else:
                return error_login_failed()
        self.user = user
        with server_lock:
            connected_users.add(user_id)
        self.login_history.succeed_user(user_id, save=True)
        return process_login_success(user_id)

def prompt_request(self, lines, prompt: str, choices: dict):
    self._send_data(Message(lines=lines, prompt=prompt, choices=choices))
    return self._receive_data()

    # base implementation for when testing net_client/net_server
    # NOTE: must be overridden by actual app (see client/server)

def init_success_lines(self):
    """OVERRIDE in subclass
    First server message lines that user sees.  Should tell them to log in.
    """
    return ['Generic Server.', 'Please log in.']

def login_fail_lines(self):
    """OVERRIDE in subclass
    Login failure message lines back to user.
    """
    return ['please try again.']

def process_login_success(self, user_id):
    """OVERRIDE in subclass
    First method called on successful login.
Should do any user initialization and then return Message.
    """
    return Message(lines=[f"Welcome {user_id}."])

def process_message(self, data):
    """OVERRIDE in subclass
Called on all subsequent Cmd messages from client.
    Should do any processing and return Message.
    """
    if 'text' in data:
        cmd = data['text'].split(' ')
        if cmd[0] in ['bye', 'logout']:
            return Message(lines=["Goodbye."], mode=Mode.bye)
        else:
            return Message(lines=["Unknown command."])

def start(host, port, _id, key, protocol, handler_class):
    global server_id, server_key, server_protocol
    server_id = _id
    server_key = key
    server_protocol = protocol
    with Server((host, port), handler_class) as server:
        logging.info("Server.start: server running (%s:%s)" % (host, port))
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        running = True
        while running:
            text = input()
            if text in ['q', 'quit', 'exit']:
                running = False
        server.shutdown()
        logging.info('server shutdown.')


if __name__ == '__main__':
    """a test of the stub net server"""
    host = 'localhost'
    start(host, nc.Test.server_port, nc.Test.id, nc.Test.key, nc.Test.protocol,
          UserHandler)
