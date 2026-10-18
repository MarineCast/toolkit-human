# Retained original census sources

Keep provider originals locally, outside Git. The [packaged acquisition recipe](../config/census-sources.v1.json) is the authoritative source of paths, URLs and SHA256 pins. Paths are relative to `HUMAN_WORKSPACE` or the workspace argument, independent of the recipe file's location. `data/` is ignored by Git; commit this documentation, recipe and code, not archives or extracted bulk data. `Data/Human` is reserved for the three final Parquets.

## Canadian 2021 originals

Use one canonical folder, `data/raw/statcan/2021/`, inside the Human workspace. Retain the original ZIP bytes and provider filenames:

| Original | Canonical workspace-relative path | Expected SHA256 |
|---|---|---|
| National 2021 cartographic dissemination-area boundaries | `data/raw/statcan/2021/lda_000b21a_e.zip` | `cb8e00a1165d8f309a62884860112944ea5acc177bb926d966b4a51fbd0c9ecb` |
| BC 2021 comprehensive Census Profile, English CSV | `data/raw/statcan/2021/98-401-X2021006_BC_CB_eng_CSV.zip` | `067f53dfefece572dc9c83d24d90226cc7b7e217b90c4f724a21ffd90457f36f` |

Official download URLs:

- [2021 cartographic DA boundary ZIP](https://www12.statcan.gc.ca/census-recensement/2021/geo/sip-pis/boundary-limites/files-fichiers/lda_000b21a_e.zip).
- [BC comprehensive Census Profile ZIP](https://www12.statcan.gc.ca/census-recensement/2021/dp-pd/prof/details/download-telecharger/comp/GetFile.cfm?Lang=E&FILETYPE=CSV&GEONO=006_BC_CB).

The boundary ZIP contains `lda_000b21a_e.shp` and its DBF/SHX/PRJ/XML companions, with DAUID, DGUID and PRUID attributes in Statistics Canada's Lambert projection (EPSG:3347). It is the **cartographic** DA boundary file; do not replace it with a digital boundary, another geography level or another census vintage. The BC Profile ZIP contains one `*_English_CSV_data_BritishColumbia.csv` plus metadata and geography-index files. The adapter streams dissemination-area rows for characteristic 1 (`Population, 2021`) and joins by DGUID; suppression remains missing.

Download without automatic unpacking/deletion. Safari may expand ZIPs into folders and put the original archives in Trash when “Open safe files after downloading” is enabled. Restore those original ZIPs to Downloads, or turn off automatic opening and download again. An extracted folder, or a ZIP recreated from it, cannot establish the pinned original archive's checksum. Do not silently update pins to accept a new download.

Verify each ZIP before moving it: `shasum -a 256 /path/to/original.zip`, then `unzip -t /path/to/original.zip`. Inspect the member names/vintage above. A hash mismatch means these are different bytes, even if the filenames agree; preserve them and investigate provider revisions or a different selection before deciding on a new source version. Move verified originals to the canonical paths; avoid keeping a second retained ZIP copy. Do not unpack the multi-GB Profile into the repository. Source archives remain outside intermediate cleanup.

## Other inputs and local-first behavior

The recipe also pins US WA/OR TIGER2020 block ZIPs with `POP20` and the 2024 US state inventory under `data/sources/census-us2020-ca2021/us/`. Their exact URLs and hashes remain in the same recipe. This storage update changes neither their paths nor the five source identities. All five originals are needed for a fresh normalization; Canadian archives alone do not certify that rebuild. The optional retained GAF CSV is an audit input and is not a substitute for the pinned Profile ZIP.

For every original, acquisition first checks the configured local path and SHA256. A match is reused before any request or download-budget consumption, even in live mode. Reuse does not invent a provider download timestamp. A mismatch fails and preserves the existing file; it never triggers replacement. A missing offline input gives its required path and expected hash without creating directories. Live mode tries the pinned URL within the configured budgets; an HTTP failure reports manual placement instructions. Browser downloads can work even when programmatic endpoints return HTTP 403.

The main pipeline may reuse a verified normalized census bundle first; when that bundle's location is missing, its raw-source fallback uses these canonical originals. A present corrupt normalized bundle fails. Verifying local archive reuse does not run normalization, population allocation, native collection or final publication, and does not alter the approved Data/Human products. Earlier retained-normalization parity tests remain separate evidence from any future raw-source rebuild.


## When only extracted downloads remain

Original ZIP hashes/CRC cannot be recovered by recompressing folders. The supported alternative registers the extracted files directly, with **no new archive or duplicate retained source copy**:

```sh
python -m human.pipelines.population.local_sources --workspace . \
  --geography-dir "$HOME/Downloads/lda_000b21a_e" \
  --profile-dir "$HOME/Downloads/98-401-X2021006_BC_CB_eng_CSV"
```

Use the actual current folder paths. Registration requires both folders before moving anything. It checks the cartographic DA shapefile companions, EPSG:3347, sampled 2021 DA identifiers, and the comprehensive Profile CSV's 2021 header/first record. It hashes every regular file and moves the folders on the same filesystem into `data/raw/statcan/2021/lda_000b21a_e/` and `data/raw/statcan/2021/98-401-X2021006_BC_CB_eng_CSV/`. Existing destinations are never replaced. A failure rolls back moves and removes only its newly created receipts.

Immutable `*.extracted.receipt.json` files record member hashes/lengths, observed registration time, user-supplied extraction origin, original provider URL/ZIP hash and the explicit absence of the original archive. Registration time is not a provider download timestamp. A private ignored `data/raw/statcan/2021/census-sources.local.json` pins the extracted primary files and their receipts. It uses `population_format: statcan_profile_2021` so the multi-GB CSV is streamed in chunks with exactly the same DA/Population-2021 row selection as the ZIP reader. Normalization verifies every companion file, not just the SHP. The provider ZIP URL is not used to download a different extracted representation.

The main recipe declares `population.local_specification`. If a verified normalized bundle is unavailable, an existing local specification takes precedence over the original-ZIP specification. A present corrupt local recipe or receipt/member fails; it never silently falls back to other source bytes. If no local recipe exists, the original five-ZIP recipe remains the fallback. Original upstream ZIP pins remain unchanged. This is a distinct user-supplied source representation, not byte-identical upstream archive recovery. Registration and bounded reuse checks do not establish a complete numerical census rebuild or alter current Data/Human outputs.
