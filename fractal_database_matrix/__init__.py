import logging
import os

HOMESERVER_URL = os.environ.get("MATRIX_HOMESERVER_URL")

logger = logging.getLogger(__name__)
