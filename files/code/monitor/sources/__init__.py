"""Market-input fetchers. Each module has INPUT (the model input key it feeds, from model/build_model.py) and
fetch(model_value, log) -> list of records (see common.record). To add a source, write one module and list it here."""
from . import eurostat, wholesale

SOURCES = [eurostat, wholesale]
