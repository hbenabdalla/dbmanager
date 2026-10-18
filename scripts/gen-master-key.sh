#!/usr/bin/env sh
python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
