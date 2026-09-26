.PHONY: test serve example

test:
	python3 -m unittest discover -s tests -v

serve:
	schemasift --config schemasift.yaml serve

example:
	schemasift --config schemasift.example.yaml select examples/request.json

