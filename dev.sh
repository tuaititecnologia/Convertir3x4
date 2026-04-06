#!/bin/bash
# Hot-reload dev mode: restarts the app when .py files change
ls *.py | entr -r ./venv/bin/python Convertir3x4.py
