# Personal vault security

The personal vault is isolated in `vault.sqlite3` and `vault-keys/`. Provider
credentials use a different `provider-keys/` encryption key. Deleting the vault
does not delete or activate provider configuration.

Each field value uses AES-256-GCM with a fresh random 96-bit nonce. The field's
opaque ID is authenticated as associated data: swapping ciphertext between
fields fails authentication. Grant call, recipient, and purpose scopes are also
encrypted, with associated data binding the grant and field IDs. Profile names,
field keys, labels, categories, timestamps and opaque IDs are metadata. Do not
put personal values in these metadata names.

The random data key is protected by current-user Windows DPAPI by default.
Explicit passphrases select a portable scrypt (N=32768, r=8, p=1) derived wrapping
key and AES-GCM envelope. On Linux, without a supplied passphrase, an OS-backed
Secret Service/KWallet keyring is required; plaintext and third-party fallback
keyrings are refused. No plaintext data key is written to disk. Key setup is
lazy: the dashboard can operate without an unlocked vault. Decryption never
creates a missing key. A lost key or incorrect passphrase cannot recover values.
The application supplies passphrases through its configured runtime environment;
protect that environment and prefer the OS key store when available.

Private storage directories use POSIX 0700 and files 0600. Windows applies a
protected DACL granting full access only to the current process user and Local
System. Path ancestry rejects symlinks and Windows reparse points; database,
journal and key files reject hardlinks as well. Filesystem checks and permissions
protect against other ordinary OS users. Code running as the same OS user or an
administrator can read process memory or replace application code and remains
outside this local vault boundary.

All fields, including explicit ordinary fields, are masked in list responses.
The caller must enforce fresh operator reauthentication before `reveal` and
`grant`; the HTTP API owns that session boundary. Typed categories are stored
separately and conservatively normalize sensitivity to sensitive. An explicit
ordinary label is upgraded for known government, health, insurance, financial
and credential field names. Categories and sensitivity never bypass grants.

Disclosure requires an exact field, active call ID, exact recipient, exact
purpose and expiry of at most 300 seconds. Runtime supplies the trusted call
and recipient; this method must never be exposed as a model tool accepting
self-asserted call/recipient identity. An unknown field fails closed. Value
updates revoke existing grants. SQLite immediate transactions serialize scope
checks, decryption, revocation and deletion across instances, so revocation
cannot commit between a permission check and that disclosure. A disclosure
already completed before revocation cannot be recalled.

Audit records contain fixed action/decision names, timestamps and generated
opaque IDs. Caller-supplied values, purposes, recipients, field labels and keys
are deliberately omitted, so secret-bearing scope text cannot enter audit.
Allowed and denied reveals/disclosures, grants, revocations, updates and
deletions are recorded. No values are logged by the vault.

`redact(text)` replaces configured exact values and normalized digit forms
containing spaces, hyphens, Unicode dashes or parentheses. Short values use token
boundaries to avoid corrupting unrelated words. Patterns escape literal values.
If any configured field is locked or corrupt, redaction returns the constant
`[redacted: vault unavailable]`, replacing the entire proposed transcript.
This is defense in depth: spelled-out digits, paraphrases, unknown values and
information already spoken to a recipient cannot be reliably recovered or
redacted. The full vault must never be copied into model prompts.

Individual deletion removes a field and its grants under `secure_delete`, then
checkpoints and vacuums SQLite. Complete deletion also removes the protected
vault data key, without caching decrypted keys between operations. It preserves
value-free audit history. Overwriting/removing the wrapped-key file is best
effort: SSD wear leveling, filesystem snapshots, backups and previously copied
keys need independent retention/deletion controls. Restoring a backup that
includes its protected key restores the ability to decrypt that backup.
