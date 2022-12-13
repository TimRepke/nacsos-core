echo "Stopping NACSOS-core service"
syetemctl stop nacos-core

echo "Becoming 'nacsos' user"
su - nacsos
cd /home/nacsos/nacsos-core

echo "Dropping virtual environment"
rm -rf venv

echo "Fetching updated source"
# "reset" softly by stashing (in case files changed)
git stash
# pull from origin (production branch)
git pull origin production

echo "Creating new virtual environment"
python3.10 -m venv venv
source venv/bin/activate

echo "Installing requirements"
pip install -r requirements.txt

echo "Exiting 'nacsos' user scope"
exit

echo "Starting NACSOS-core service"
systemctl start nacsos-core
