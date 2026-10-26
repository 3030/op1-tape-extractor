# Mac App Store Setup Guide

This document explains how to set up the GitHub secrets needed for Mac App Store submission.

## Overview

The workflow now builds **two separate packages**:

1. **Developer ID DMG** (existing): For direct distribution outside the Mac App Store
   - Uses `entitlements.plist` with PyInstaller-compatible entitlements
   - Notarized by Apple
   - Distributed as `.dmg` file

2. **Mac App Store PKG** (new): For App Store submission via Transporter
   - Uses `entitlements-appstore.plist` with sandbox-only entitlements
   - No audio entitlements (not needed for playback)
   - Distributed as `.pkg` file for Transporter upload

## Required Certificates

You need **four certificates** from Apple Developer:

### Already Have (for Developer ID path):
1. **Developer ID Application** - Signs the app for notarization
2. **Developer ID Installer** (optional) - For PKG outside App Store

### Need to Add (for Mac App Store path):
3. **Apple Distribution** (or "Mac App Distribution") - Signs the app for App Store
4. **3rd Party Mac Developer Installer** - Signs the PKG for App Store

## Required GitHub Secrets

### Existing Secrets (keep as-is):
- `CERTIFICATES_P12` - Developer ID Application cert (base64)
- `CERTIFICATES_P12_PASSWORD` - Password for above
- `KEYCHAIN_PASSWORD` - Temporary keychain password
- `APPLE_ID` - Your Apple ID email
- `APPLE_ID_PASSWORD` - App-specific password for notarization
- `APPLE_TEAM_ID` - Your 10-character Team ID

### New Secrets to Add:

#### `APPSTORE_CERT_P12`
Your "Apple Distribution" certificate exported as .p12, then base64 encoded.

```bash
# Export cert from Keychain Access as "AppleDistribution.p12"
base64 -i AppleDistribution.p12 | pbcopy
# Paste into GitHub secret
```

#### `APPSTORE_CERT_PASSWORD`
The password you set when exporting the Apple Distribution .p12 file.

#### `INSTALLER_CERT_P12`
Your "3rd Party Mac Developer Installer" certificate exported as .p12, then base64 encoded.

```bash
# Export cert from Keychain Access as "InstallerCert.p12"
base64 -i InstallerCert.p12 | pbcopy
# Paste into GitHub secret
```

#### `INSTALLER_CERT_PASSWORD`
The password you set when exporting the Installer .p12 file.

#### `PROVISIONING_PROFILE`
Your Mac App Store provisioning profile, base64 encoded.

1. Download your provisioning profile from Apple Developer portal
2. Create a profile for your app's bundle ID: `com.3030.op1loopextractor`
3. Encode it:

```bash
base64 -i YourProfile.provisionprofile | pbcopy
# Paste into GitHub secret
```

## How to Get the Certificates

1. Go to https://developer.apple.com/account/resources/certificates/list
2. Click "+" to create new certificates
3. Choose:
   - "Apple Distribution" for the app signing cert
   - "Mac Installer Distribution" for the pkg signing cert
4. Download the certificates
5. Double-click to install in Keychain Access
6. Export as .p12 files (right-click → Export)
7. Base64 encode and add to GitHub secrets

## How to Get the Provisioning Profile

1. Go to https://developer.apple.com/account/resources/profiles/list
2. Click "+" to create a new profile
3. Choose "Mac App Store"
4. Select your App ID (or create one for `com.3030.op1loopextractor`)
5. Select your "Apple Distribution" certificate
6. Download the `.provisionprofile` file
7. Base64 encode and add to GitHub secret

## Testing the Build

Once all secrets are configured:

1. Push a commit or create a tag
2. The workflow will build both packages:
   - `OP-1-Loop-Extractor-macOS.dmg` (Developer ID - for direct distribution)
   - `OP-1-Loop-Extractor-AppStore.pkg` (Mac App Store - for Transporter)

3. Download the `.pkg` artifact from GitHub Actions
4. Upload to App Store Connect using Transporter or:
```bash
xcrun altool --upload-app --type macos --file "OP-1-Loop-Extractor-AppStore.pkg" \
  --apiKey YOUR_API_KEY --apiIssuer YOUR_ISSUER_ID
```

### Xcode signing checklist
- Import the `.app` from `mac-appstore.spec` into an Xcode project for final signing.
- Enable Hardened Runtime and App Sandbox; use `entitlements-appstore.plist` as the entitlements file.
- Only keep `com.apple.security.files.user-selected.read-write` for file access; audio playback uses QtMultimedia and does not require extra entitlements.
- Ensure every embedded Qt framework/plugin and libsndfile dylib is signed by Xcode before archiving.

## Key Differences: entitlements.plist vs entitlements-appstore.plist

### `entitlements.plist` (Developer ID - Notarization)
```xml
<key>com.apple.security.cs.disable-library-validation</key>
<true/>
<key>com.apple.security.cs.allow-unsigned-executable-memory</key>
<true/>
```
- Allows PyInstaller's unsigned Python frameworks
- NOT allowed in Mac App Store

### `entitlements-appstore.plist` (Mac App Store)
```xml
<key>com.apple.security.app-sandbox</key>
<true/>
<key>com.apple.security.files.user-selected.read-write</key>
<true/>
```
- Sandbox required for App Store
- Only user-selected file access (for loading OP-1 tapes)
- **NO audio entitlements** - not needed for playback

## Common Issues

### "The bundle does not support App Sandbox"
- Make sure `entitlements-appstore.plist` has `com.apple.security.app-sandbox` = true

### "Invalid entitlement: com.apple.security.device.audio-output"
- This entitlement is NOT supported on macOS App Store
- It's been removed from `entitlements-appstore.plist`
- Audio playback works without entitlements

### "Could not find the provisioning profile"
- Ensure `PROVISIONING_PROFILE` secret is set and base64 encoded
- The profile must match your bundle ID and Team ID

### "No identity found"
- Check that all certificates are properly imported
- Verify certificate names match exactly (including your name and Team ID)

## References

- [App Sandbox Entitlements](https://developer.apple.com/documentation/security/app_sandbox)
- [Notarizing macOS Software](https://developer.apple.com/documentation/security/notarizing_macos_software_before_distribution)
- [Distributing to the Mac App Store](https://developer.apple.com/documentation/xcode/distributing-your-app-to-registered-devices)
