PY=.venv/bin/python
# Full refresh: ~15 min of polite Realtor requests (cached per day), then seconds to build.
all: fetch build
fetch:
	caffeinate -i $(PY) scripts/fetch_search.py
	caffeinate -i $(PY) scripts/fetch_details.py
	$(PY) scripts/fetch_permits.py
county:
	$(PY) scripts/build_county.py
build:
	$(PY) scripts/build_index.py
deploy:
	$(PY) scripts/deploy.py
