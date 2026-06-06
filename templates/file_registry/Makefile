.PHONY: build build-frontend deploy deploy-ic test clean

# Mainnet backend canister id — injected into the frontend build so it talks
# to the deployed registry. Override on the command line for other targets.
FILE_REGISTRY_BACKEND_ID ?= od2mb-wyaaa-aaaac-qgmza-cai

# Build the Basilisk backend WASM. icp-cli's prebuilt recipe then installs
# the artifact at .basilisk/ic_file_registry/ic_file_registry.wasm (see icp.yaml).
build:
	CANISTER_CANDID_PATH=./ic_file_registry.did python3 -m basilisk ic_file_registry src/main.py

# Build the SvelteKit frontend into frontend/dist. Set VITE_CANISTER_ID to the
# backend canister id so the UI targets the right canister.
build-frontend:
	npm --prefix frontend ci
	npm --prefix frontend run build

# Local deploy (backend + frontend).
deploy: build build-frontend
	icp deploy

# Mainnet deploy — point the frontend at the mainnet backend canister.
deploy-ic: build
	VITE_CANISTER_ID=$(FILE_REGISTRY_BACKEND_ID) $(MAKE) build-frontend
	icp deploy -e ic

test:
	pytest -q

clean:
	rm -rf .basilisk frontend/dist frontend/.svelte-kit frontend/node_modules
