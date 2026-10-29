# Product delivery

The product version is defined by package.json and synchronized with the executable,
six Skills, CHANGELOG and release/PROFILE.json. The installation targets and known
platform-specific operations are documented in ../COMPATIBILITY.md. Use
../INSTALLATION.md for package selection and configuration.

## Package identity

A delivery binds the exact source commit, native executable, OS/CPU, resource
manifests and acceptance results. An earlier package's acceptance is not inherited
by a new binary. DELIVERY-BINDING.json records candidate identity, not a publication
receipt. Missing or pending records are not successful checks.

## Distribution configuration

The product has no company-specific publishing endpoint, recipient group or Hub ID.
Keep channel endpoints, authentication, recipient mappings and publication receipts
outside the product repository in a privately managed deployment workspace. Do not
put credentials or private download links in Skills or public documentation.

The existing package_internal_release.py command and internal_delivery output key
are retained for compatibility. They do not select a company or grant distribution
rights. The current SDK distribution permission and third-party notices remain authoritative.
See ../../NOTICE.md; an actual publication still requires its separate release checks.

## Acceptance scope

Use the supported subset in release/PROFILE.json and the current executable's
modules/reference. Preserve the observed official ERC/DRC results and custom-check
differences. Installation, save/reopen, advanced recovery, Agent scenarios and
manufacturing approval are distinct checks. Source tests do not qualify an unbuilt
package. No publication or manufacturing signoff follows from this document.
