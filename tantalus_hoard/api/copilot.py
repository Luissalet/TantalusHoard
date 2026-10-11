"""Mount the family relay; Faustus owns conversation, model and execution."""
import os
from ..hoard_link.config import LinkConfig
from ..hoard_link.copilot import make_copilot_router


def create_router(config):
    link = LinkConfig.load(path=config.backend_json_path, app='tantalus')
    url = os.environ.get('TANTALUS_FAUSTUS_URL') or os.environ.get('HOARD_FAUSTUS_URL')
    return make_copilot_router('tantalus', config.data_dir,
        faustus_urls=[url] if url else link.faustus_urls, token=link.faustus_token,
        app_name="Tantalus's Hoard")
