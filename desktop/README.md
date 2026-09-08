# SmorX PPAP desktop client

Build a Windows EXE that talks to your live API:

```powershell
cd desktop
.\build-exe.ps1 -ApiUrl "https://your-api-host/api"
```

Output: `desktop/dist-exe/SmorX-PPAP.exe` and `SmorX-Setup.exe` (portable copy).

Customer flow:
1. Open EXE — Activate shows Device ID + Host name
2. Send those to Super Admin
3. Super Admin creates the customer in the web app
4. Customer enters Install password, License key, Email, Temporary password → Activate
5. Login → change password when prompted
