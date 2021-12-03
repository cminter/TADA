
import enum

# shared client/server parameters used at connection init so we can
# more quickly drop bogus connections and know whether client and
# server are using incompatible versions
server_port = 5000
app_id = 'TADA'
app_key = '1234567890'
app_protocol = 1

class K(str, enum.Enum):
    """keys for dictionary use, so that we can avoid 'stringly' typed
    anti-pattern.  When adding new entries make sure the key matches
    the string.

    (see https://www.google.com/search?q=%22stringly%22+typed)
    """
    id = 'id'
    name = 'name'
    exits = 'exits'
    password = 'password'
    money = 'money'
    room = 'room'
    room_name = 'room_name'
    health = 'health'
    xp = 'xp'

