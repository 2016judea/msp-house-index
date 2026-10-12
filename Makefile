PY=.venv/bin/python
# Full refresh: ~15 min of polite Realtor requests (cached per day), then seconds to build.
all: fetch build
fetch:
	caffeinate -i $(PY) scripts/fetch_search.py
	caffeinate -i $(PY) scripts/fetch_details.py
	$(PY) scripts/fetch_city_records.py
	$(PY) scripts/fetch_permits.py
county:
	$(PY) scripts/build_county.py
build:
	$(PY) scripts/build_condition.py
	$(PY) scripts/build_index.py
	$(PY) scripts/build_block.py
	$(PY) scripts/build_records.py
	$(PY) scripts/build_timeline.py
	$(PY) scripts/build_map.py
	$(PY) scripts/build_public.py
map:
	$(PY) scripts/build_map.py
	$(PY) scripts/build_public.py
deploy:
	$(PY) scripts/deploy.py
