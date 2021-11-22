#!/bin/env python3

import socketserver
import json
from dataclasses import dataclass, field
import enum

import net_common as nc

K = nc.K
Mode = nc.Mode

# fake data 
roomsData = {
    'ul': {K.name: 'Upper Left',  K.exits: {'s': 'll', 'e': 'ur'}},
    'ur': {K.name: 'Upper Right', K.exits: {'s': 'lr', 'w': 'ul'}},
    'll': {K.name: 'Lower Left',  K.exits: {'n': 'ul', 'e': 'lr'}},
    'lr': {K.name: 'Lower Right', K.exits: {'n': 'ur', 'w': 'll'}},
}
usersData = {
    'ryan': {K.password: 'swordfish', K.money: 1000, K.room: 'ul'},
}

compass_txts = {'n': 'North', 'e': 'East', 's': 'South', 'w': 'West'}

@dataclass
class Room(object):
    name: str
    exits: dict

    def exitsTxt(self): 
        exit_txts = []
        for k in self.exits.keys():
            if k in compass_txts:  exit_txts.append(compass_txts[k])
        return ", ".join(exit_txts)

@dataclass
class User(object):
    name: str
    password: str
    money: int
    room: str

@dataclass
class Message(object):
    lines: list
    mode: Mode = Mode.cmd
    error: int = 0
    error_line: str = ''

rooms = {}
for id, info in roomsData.items():
    room = Room(name=info[K.name], exits=info[K.exits])
    rooms[id] = room
users = {}
for name, info in usersData.items():
    user = User(name=name, password=info[K.password], money=info[K.money],
            room=info[K.room])
    users[name] = user

class PlayerServer(socketserver.BaseRequestHandler):
    def handle(self):
        self.sender = self.client_address[0]
        self.ready = None
        self.user = None
        print(f"connect (addr={self.sender})")
        running = True
        while running:
            try:
                request = nc.fromJSONB(self.request.recv(1024))
                if request is None:
                    running = False
                    break
                try:
                    response = self.processMessage(request)
                except Exception as e:
                    print(e)
                    self.sendData(Message(lines=["server side error"], error=1))
                self.sendData(response)
            except:
                print("WARNING: ignore malformed JSON")
                self.sendData(Message(lines=["malformed JSON"], error=1))
        print(f"disconnect {self.user.name} (addr={self.sender})")

    def sendData(self, data):
        self.request.sendall(nc.toJSONB(data))

    def roomMsg(self, lines=[]):
        room = rooms[self.user.room]
        room_name = room.name
        exitsTxt = room.exitsTxt()
        lines2 = list(lines)
        lines2.append(f"You are in {room_name} with exits to {exitsTxt}")
        return Message(lines=lines2)

    def processMessage(self, data):
        if self.ready is None:  # assume init message
            if 'app' in data:
                if data['app'] == 'TADA':
                    #TODO: verify key is expected and protocol match
                    self.ready = True
                    return Message(lines=['Welcome!', 'Please log in.'], mode=Mode.login)
                else:
                    return {'eol'} # poser, ignore them
            else:
                return {'eol'} # poser, ignore them
        if self.user is None:
            user_id, password = data['login']
            if user_id not in users:
                #TODO: check password
                # when failing don't tell that have wrong user id
                return Message(error_line='Login failed.', error=1,
                        lines=['please try again.'], mode=Mode.login)
            else:
                self.user = users[user_id]
                print(f"login {self.user.name} (addr={self.sender})")
                money = self.user.money
                lines = [f"Welcome {self.user.name}.", f"You have {money} gold."]
                return self.roomMsg(lines)
        if 'cmd' in data:
            cmd = data['cmd'].split(' ')
            #TODO: handle all commands (would be more sophisticated, e.g. proper parser)
            if cmd[0] in compass_txts:  cmd.insert(0, 'go')
            print(f"{cmd}")
            if cmd[0] in ['g', 'go']:
                direction = cmd[1]
                room = rooms[self.user.room]
                if direction in room.exits:
                    self.user.room = room.exits[direction]
                else:
                    return Message(lines=["You cannot go that direction."])
                return self.roomMsg()
            if cmd[0] in ['look']:
                return self.roomMsg()
            if cmd[0] in ['bye', 'logout']:
                return Message(lines=["Bye for now."], mode=Mode.bye)
            if cmd[0] in ['help', 'cheatcode']:
                return Message(lines=["Wouldn't that be nice."])
            else:
                return Message(lines=["I didn't understand that.  Try something else."])

def startServer(host, port):
    with socketserver.TCPServer((host, port), PlayerServer) as server:
        print(f"server running ({host=}, {port=})")
        server.serve_forever()

if __name__ == "__main__":
    host = "localhost"
    startServer(host, nc.serverPort)

