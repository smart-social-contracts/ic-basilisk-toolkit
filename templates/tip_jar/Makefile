.PHONY: build deploy deploy-ic test clean

# Build the Basilisk backend WASM. icp-cli's prebuilt recipe then installs
# the artifact at .basilisk/tip_jar_backend/tip_jar_backend.wasm (see icp.yaml).
build:
	CANISTER_CANDID_PATH=./tip_jar_backend.did python -m basilisk tip_jar_backend src/backend/main.py

# Local deploy (backend + frontend). The frontend is served by the
# asset-canister recipe (see icp.yaml).
deploy: build
	icp deploy

# Mainnet deploy.
deploy-ic: build
	icp deploy -e ic

test:
	pytest -q

clean:
	rm -rf .basilisk
