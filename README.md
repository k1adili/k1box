# K1box

A simple English-language web app for storing and sharing files on a local network, built with Flask and SQLite. The interface is left-to-right.

## Features

- Create an account and log in with a hashed password
- Upload, download, and delete files in a personal space
- Share private files with other accounts
- Browse and download public files, even without logging in
- Choose whether a file is public or private during upload and change its visibility later
- Administrator panel for managing accounts, granting administrator access, resetting passwords, and managing all files
- Upload up to 5 files at a time, with no file-size limit configured by the app
- Upload progress shown in the browser

## Administrator account

On first launch, K1box creates the administrator account `admin` with the temporary password `admin`. The administrator must change this password at first login before using the site. After logging in, open **Admin panel** to manage user accounts and uploaded files. Password resets require the affected user to change the temporary password at their next login.

Change the default password immediately and do not expose the server to an untrusted network.

## Setup

Python 3.10 or later is required. From the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

By default, the app runs on all network interfaces on port `5000`. On the same computer, open `http://127.0.0.1:5000`. To access it from other devices on the network, find the host computer's local IP address and open it in a browser on that device; for example, `http://192.168.1.20:5000`. If needed, allow port 5000 through the private-network firewall.

To keep sessions valid after restarting the app, set a persistent key before launch:

```powershell
$env:K1BOX_SECRET_KEY = "a-long-random-value"
python app.py
```

The port can be changed with `K1BOX_PORT`. The database and uploaded files are stored in `instance\k1box.sqlite3` and `instance\uploads`, respectively. The app does not impose an upload-size limit, but available disk space, the browser, or external web-server/proxy configuration may still limit uploads.

## Access note

Anyone on the network who knows the server address can view and download public files. Private files are available only to their owner and users with whom the owner has shared them. Account creation is open to users on the network; share the registration link only on a network you trust.

This version is intended for simple use on a local network. If you expose the server beyond a trusted network, add authentication and operational configuration appropriate for your environment first.

## Run the tests

```powershell
python -m unittest discover -s tests -v
```
