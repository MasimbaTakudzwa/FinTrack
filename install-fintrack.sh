
#!/bin/bash
# install-fintrack.sh

echo "Setting up FinTrack development environment..."

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip setuptools wheel

# Install requirements in order
echo "Installing core dependencies..."
pip install -r requirements-minimal.txt

echo "Installing ML dependencies..."
pip install -r requirements-ml.txt

echo "Installing development tools..."
pip install -r requirements-dev.txt

# Install frontend dependencies (if Node.js is available)
if command -v npm &> /dev/null; then
    echo "Installing frontend dependencies..."
    cd frontend
    npm install
    cd ..
fi

# Create environment file
if [ ! -f .env ]; then
    echo "Creating .env file..."
    cp config/environment/.env.example .env
    echo "Please edit .env file with your configuration"
fi

# Set up database (PostgreSQL required)
echo "Setting up database..."
# Note: You'll need to manually create the database
# sudo -u postgres createdb fintrack
# sudo -u postgres createuser fintrack_user

echo "Installation complete!"
echo "Next steps:"
echo "1. Edit .env file with your settings"
echo "2. Run: python manage.py migrate"
echo "3. Run: python manage.py createsuperuser"
echo "4. Start server: python manage.py runserver"