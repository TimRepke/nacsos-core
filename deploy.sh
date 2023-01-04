#!/bin/bash
set -x

echo "Current working directory"
pwd
ls -lisah
whoami
groups

echo "Changing directory and making sure we landed there"
cd /home/nacsos/nacsos-core
pwd
ls -lisah

echo "Stopping NACSOS-core service"
sudo systemctl stop nacsos-core.service

echo "Dropping virtual environment"
rm -rf venv

echo "Fetching updated source"
git config url."https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.pik-potsdam.de/".insteadOf "ssh://git@gitlab.pik-potsdam.de/"
git stash  # "reset" softly by stashing (in case files changed)
git pull origin production  # pull from origin (production branch)

echo "Creating new virtual environment"
python3.10 -m venv venv
source venv/bin/activate
echo "Installing requirements"
pip install -r requirements.txt

echo "Handling migrations"
pip install alembic
cd venv/src/nacsos-data/
alembic upgrade head

echo "Starting NACSOS-core service"
sudo systemctl start nacsos-core.service