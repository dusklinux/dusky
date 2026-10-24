# Running setup_ssh.ps1 inside the VM

Historical instructions: `setup_ssh.ps1` is not present in this source tree.
The current guest bootstrap and Python helpers are in
[windows/guest](../../windows/guest/); see the [guest setup guide](VM_SETUP_GUIDE.md).

To configure OpenSSH on the guest Windows VM:

1. Open **PowerShell** as **Administrator** inside the Windows VM.
2. Allow PowerShell script execution for the current session:
   ```powershell
   Set-ExecutionPolicy Bypass -Scope Process -Force
   ```
3. Execute the script from the shared VirtIO-FS drive (`Z:`):
   ```powershell
   & "Z:\windows_setup\setup_ssh.ps1"
   ```
   *(Or if you copied it to the C: drive, run `& "C:\setup_ssh.ps1"`)*
