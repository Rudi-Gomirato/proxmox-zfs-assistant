# Pubblicare il progetto su GitHub

Il codice originale ha licenza MIT, scelta da Rudi Gomirato. Questa guida non crea automaticamente un repository remoto.

## Repository pubblico

Accedi come Rudi-Gomirato e apri [Nuovo repository](https://github.com/new). Scegli `proxmox-zfs-assistant`, visibilità **Public**, e lascia disattivati README, gitignore e licenza iniziali: sono già presenti localmente. Crea il repository vuoto.

Apri PowerShell nella cartella del progetto:

```powershell
Set-Location 'C:\Users\gomir\Documents\Proxmox_tools'
git init -b main
git add .
git diff --cached --stat
git status
git commit -m "Implement desktop Proxmox ZFS assistant"
git remote add origin https://github.com/Rudi-Gomirato/proxmox-zfs-assistant.git
git push -u origin main
```

Se Git richiede autore ed email, configura i tuoi dati con `git config user.name` e `git config user.email`, poi ripeti il commit. Completa l'autenticazione GitHub quando richiesta. Se `origin` esiste, controlla `git remote -v` prima di cambiarlo.

La procedura segue la [guida ufficiale GitHub](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github). Prima del commit verifica l'elenco: `.venv`, `dist`, database, chiavi e rapporti privati devono restare esclusi.

## Build e aggiornamenti

La scheda Actions esegue i test e prepara un artefatto Windows. Per una release allega lo ZIP completo e SHA256SUMS; non caricare il solo EXE. Per le modifiche successive usa `git add`, `git commit` e `git push`.
