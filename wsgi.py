import sys
import os

# Add your project directory to sys.path
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Load environment variables from .env
from dotenv import load_dotenv
load_dotenv(os.path.join(project_home, '.env'))

from app import app as application
