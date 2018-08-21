PYTHON=python3

all: build

env:
	$PYTHON -mvenv env
	./env/bin/pip install -r requirements.txt

build: env
	./env/bin/python3 -mstaticplanetscipy config.json

clean:
	rm -rf cache html

.PHONY: all build clean
