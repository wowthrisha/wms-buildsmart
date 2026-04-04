#!/bin/bash
echo "Installing dependencies..."
pip install -r requirements.txt
echo "Seeding database..."
python seed.py
echo "Starting BuildSmart..."
gunicorn --workers 2 --bind 0.0.0.0:5002 run:app
