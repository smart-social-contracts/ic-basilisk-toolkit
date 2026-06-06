# Basilisk Website

The landing page at [https://ic-basilisk.tech/](https://ic-basilisk.tech/).

Introduces [basilisk](https://github.com/smart-social-contracts/basilisk) and
[ic-basilisk-toolkit](https://github.com/smart-social-contracts/ic-basilisk-toolkit)
and links out to each live template demo.

Pure static assets — a single IC assets canister, no backend.

## Structure

```
website/
├── icp.yaml
└── src/
    └── assets/
        ├── index.html
        ├── style.css
        ├── logo.png
        └── .well-known/
            └── ic-domains    — claims ic-basilisk.tech
```

## Local preview

Any static file server works, e.g.:

```bash
cd src/assets && python -m http.server 8000
# then open http://localhost:8000
```

## Deploy to IC

Deployed with [icp-cli](https://github.com/dfinity/icp-cli) (`icp.yaml`); **dfx is not used**.

```bash
# from this directory (ic-basilisk-toolkit/website/)
icp deploy -e ic basilisk_website -y
```

Canister IDs live in `.icp/data/mappings/ic.ids.json` (committed so every deploy
targets the same canister).

### CI auto-deploy

[`.github/workflows/deploy-website.yml`](../.github/workflows/deploy-website.yml)
redeploys this canister on every push to `main` that touches `website/**`, or
on manual dispatch. It requires:

- `.icp/data/mappings/ic.ids.json` committed in `website/` (written by the first local deploy)
- Repository secret `IC_IDENTITY_PEM` — PEM-encoded identity that controls the canister

### Cycles monitoring (CycleOps)

This canister is monitored by [CycleOps](https://cycleops.dev) under the
shared team `xee7m-jddpf-rwyzl-pobzx-izlbn-vhsbt-ublzn-lf4vo-kbvz2-buwfk-xh6`
with an auto-top-up rule (threshold: 2 TC, refill to: 4 TC), matching the
convention used across all other monitored canisters.

Controllers of `basilisk_website`:

- `ah6ac-cc73l-bb2zc-ni7bh-jov4q-roeyj-6k2ob-mkg5j-pequi-vuaa6-2ae` — deploy identity
- `cpbhu-5iaaa-aaaad-aalta-cai` — CycleOps V3 blackhole (required for monitoring)

To adjust the top-up rule or remove the canister from monitoring, use the
CycleOps dashboard or the `icp canister call qc4nb-ciaaa-aaaap-aawqa-cai ...`
API pattern documented in the realms repo
(`realms/scripts/update_cycleops_thresholds.sh`).

## Custom domain migration (`ic-basilisk.tech`)

Currently `ic-basilisk.tech` points at the `tip_jar_frontend` canister
(`ox2q2-saaaa-aaaau-agj7a-cai`). Migrating it to this new canister:

1. **Deploy this canister first.** Get its id from `.icp/data/mappings/ic.ids.json` after
   `icp deploy -e ic basilisk_website`.

2. **Remove the domain from the old canister:**
   - Delete `ic-basilisk.tech` from
     `ic-basilisk-toolkit/templates/tip_jar/src/frontend/assets/.well-known/ic-domains`.
   - Redeploy the tip jar frontend: `icp deploy -e ic tip_jar_frontend`.
   - Remove the old registration from the boundary node:
     ```bash
     curl -X DELETE https://icp0.io/registrations/<REGISTRATION_ID>
     ```
     (Look up the registration id by querying `/registrations?name=ic-basilisk.tech`.)

3. **Update DNS** for `ic-basilisk.tech`:
   - `_canister-id.ic-basilisk.tech` TXT → `<new canister id>`
   - Keep the `_acme-challenge.ic-basilisk.tech` CNAME → `_acme-challenge.ic-basilisk.tech.icp2.io`
   - Keep the apex `@` CNAME/ALIAS → `icp1.io`

4. **Register the new canister** with the boundary node:
   ```bash
   curl -X POST https://icp0.io/registrations \
     -H 'Content-Type: application/json' \
     -d '{"name": "ic-basilisk.tech"}'
   ```
   Poll for status:
   ```bash
   curl https://icp0.io/registrations/<REGISTRATION_ID>
   ```
   Expect `state: Available` once the certificate is issued (usually a few
   minutes after DNS propagation).

5. **Verify** in a browser:
   ```bash
   curl -I https://ic-basilisk.tech/
   ```

See the IC docs for custom domains:
<https://internetcomputer.org/docs/building-apps/frontends/custom-domains/using-custom-domains>

## Updating template links

Template demo URLs are hard-coded in `src/assets/index.html`. Current links:

| Template | Canister id | URL |
|---|---|---|
| Tip Jar | `ox2q2-saaaa-aaaau-agj7a-cai` | <https://ox2q2-saaaa-aaaau-agj7a-cai.icp0.io/> |
| File Registry | `oe3kv-3aaaa-aaaac-qgmzq-cai` | <https://oe3kv-3aaaa-aaaac-qgmzq-cai.icp0.io/> |

When adding a new template, duplicate one of the `<a class="template">` cards
in `index.html`.
