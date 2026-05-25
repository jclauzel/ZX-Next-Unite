ZX-Next-Unite by Julien Clauzel based on:
* HDFM-GOOEY by em00k
&
* NextSync by Jari Komppa

This project is licensed under the terms of the MIT license.
ZX-Next-Unite is a Qt Application using pyside6 in Python on top of Qt6, which retains the GPLv2 Licensing (see the LICENSE file).

Videos:
* "Setup & How to": https://youtu.be/FJG-Z0DCIjQ
* Py Hdfm Gooey NextSync Head Over Heels: https://www.youtube.com/watch?v=D3_WqTPvjOE
* Py Hdfm Gooey - NextSync - Night Knight: https://www.youtube.com/watch?v=eN1eMIqMCm4&t=12s

ZX-Next-Unite provides a cross platform (Windows, Linux, MacOS) Graphical Interface that unites two great tools in a single utility: Hdfm-Gooey & Next Sync. 
* The former is mostly targeted at CSpect emulator users, and it provides a simple to use frontend interface to transfer content to an image and start the Spectrum Next emulator. 
* The latter allows hardware owners such as Kickstarter & Clones that has a Wifi/ESP module to synchronize content directly from a machine over the network to their Next machines.

The first tab of ZX-Next-Unite is aimed at CSpect emulator users and developers. In that section you can mount an HDF image used by CSpect and upload download content using a built simple file explorer. Once the content is uploaded to the image you can then start CSpect directly from it and therefore exposes a simple to use frontend interface to the emulator. Key settings such as screen size can be directly adjusted, it avoids the need to know command line options to start using your emulated Spectrum Next.

The second tab is designed for real hardware owners such as KS1, KS2 or other clones that also has an ESP (WIFI module). Jari designed a while back a custom protocol called NextSync that allows to synchronize an entire folder from a machine (PC/MAC/Linux) that resides on a remote machine back to the Spectrum Next over the network (it is one way only). This utility implements the "server" side the program that will 'listen' and waits for incoming connections from your Next. On your Next machine you will run the custom dot ".sync" or ".syncfast" command that connects to the server here ZX-Next-Unite. To set this up you will need first to download Jari zip package. The latest release v1.2 of the dot .sync command can be found here https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 . 

Download Jari latest release and inside the zip dot folder you will find a file called "SYNC" and "SYNCFAST" (that allows faster transfer rates). Extract and copy that file in the /dot folder located on the root of your Spectrum Next SD card and boot it up as usual. Make sure WIFI is fully setup normally, that you can access the internet and your machine that will be running Py Hdfm gooey are on the same IP network and that no firewall blocks incoming communication on your "receiving" machine. By default, the custom protocol works on TCP port 2048.
Start, python ZX-Next-Unite.py and navigate to NextSync tab. On the built in Explorer select the root folder you'd like to sync and hit the "Prepare NextSync server" button. At this point the server is ready to go but not running yet, this allows you to review the above log. The first time I recommend that you select the "Sync once" option so the server will stop after the first .sync run (once .sync is working use .syncfast instead if possible). 
Since this is the first time you will run the .sync command on your Spectrum Next, the command will ask you to input what IP address it needs to connect to. In the log Window you should the machine Primary IP address (on non Windows system you may need to run ipconfig/ifconfig to see all available IPs). Your machine DHCP address usually starting by 10.0.x.y or 192.168.x.y, select the 4x digit IP address digit you are going to use for the upcoming step below.

On your Spectrum Next, select "Command Line" option and then simply type .sync and press enter. At this point as mentioned above it will request what IP address to connect to, so input the ip address selected above and press enter. 
On the next this configuration will be retained into C:/sys/config/nextsync.cfg if you need to change it later either edit the config file or type: .sync followed by the new IP address you wish to use to connect to for example: .sync 10.0.0.15 (it is the Next that will issue a connection to the PC/MAC/Linux).

That's it you should now be all go to go.

When ready hit the "Yes, start NextSync server" button and on your next type ".sync" again. If everything is set up properly you should now see your next downloading all the files and once done the .sync command will exit. From now on you may use .syncfast instead for faster transfer rates if your network allows it.

There are many other option & features in the tool for example once the first full sync has been done it will create a SyncPoint file so it knows only what needs to be done and synced the next time you run the .sync command. If such a file exists, a button will appear that allows you to delete it in order that you can perform a full new sync if need be. A create SyncIgnore button will appear if such a file is not present. These file types allow you to control what file extensions will not be synced and send to the remote machine.

For more information, please check the "Help" tab in the tool or below.

Installation
------------
There is no need for an executable as the main ZX-Next-Unite.py file mostly needs a recent Python interpreter to run. 
Clone the repository, install a recent Python interpreter, install PySide6 Python library and you're good to go. 

For a full install overview feel free to follow this "Setup & How to" video: https://youtu.be/FJG-Z0DCIjQ

Requirements
------------
- ZX-Next-Unite.py either download the latest release here or clone this repo, locate where this file is located that is main tool (there are no executable as it is a Python program).
- Python 3.7+ or higher (the latest version is recommended)
- PySide6
- CSpect emulator by Mike Dailly installed in local directory please download from http://www.cspect.org
    feel free to support his development efforts & patreon https://www.patreon.com/mikedailly
    - Make sure Spectrum Next roms installed are installed in local directory (they should be provided in the CSpect zip package by default). 
        These two files namely: enNextZX.rom and enNxtMMC.rom -MUST- be placed in the root folder of your #CSpect.
- You will need Spectrum Next images files that you can download from https://zxspectrumnext.online/cspect/  such as http://www.zxspectrumnext.online/cspect/cspect-next-2gb.zip
- Download & install hdfmonkey by Matt Westcott https://github.com/gasman/hdfmonkey , on Windows either compile the source manually or download a pre-compiled version at: 
    https://uto.speccy.org/downloads/hdfmonkey_windows.zip
- On Mac/Linux you will need to install mono-complete

* Additional help pages:
    - https://wiki.specnext.dev/Development_Tools:Linux_setup

* Install Python from: https://www.python.org/downloads/ (the latest version is recommended)

* Install pyside6 this is required for the UI to render the different controls being used. To install pyside6 open an elevated command shell and run:

    python -m pip install pyside6

    Note: On a regular basis you may want to update the pyside6 library running:  pip install --upgrade PySide6

* Copy Cspect (with the Spectrum Next roms) and hdfmonkey in the same directory (see above). 

start using it by running: 
    python ZX-Next-Unite.py

* On Windows download and install OpenAL sound library for CSpect: https://openal.org/

* Linux (Debian) / MacOS 

Install hdfmonkey from: https://github.com/gasman/hdfmonkey

Install ZX-Next-Unite using Git:

git clone https://github.com/jclauzel/ZX-Next-Unite

python3 -m venv ./ZX-Next-Unite

source ZX-Next-Unite/bin/activate

pip install PySide6

cd ZX-Next-Unite

python3 zx-next-unite.py


If you want to start CSpect automaticaly then:

Install mono-complete (see bellow).

Install CSpect (see bellow).


CSpect 
----------

CSpect emulator by Mike Dailly installed in local directory please download from http://www.cspect.org
feel free to support his development efforts & patreon https://www.patreon.com/mikedailly

Other Mike CSpect sites and links:
https://mdf200.itch.io/cspect
https://dailly.blogspot.com/

OpenAL sound library for CSpect: https://openal.org/

HDF Monkey 
----------

If you are running the app on Windows and hdfmonkey in not present in the same directory, you will see an error message in the main log Windows as it is missing.
If that is the case you will see a 'Download and Install button' bottom right, once clicked it will try to fetch from
https://uto.speccy.org/downloads/hdfmonkey_windows.zip and unzip hdfmonkey executable in the same directory. 
If the above automated install is successful, you should then be able to select an image and navigate it.

On Mac/Linux you will need to install hdfmonkey manually based on the instructions for your platform that can be found at: https://github.com/gasman/hdfmonkey

NextSync 
--------

Py Hdfm Gooey implements the <Server> side code and protocol of NextSync by Jari Komppa.
It does not require any dot .sync modification and it uses the same very close python logic as nextsync.py.

Initial realease on specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8 
As a result you will need to run the dot same .sync command on your Next as with the console version and the same network protocol. 

The latest release v1.2 of the .sync command can be found here https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 . 

You may follow the same instructions as the provided in the readme.txt of that release.
On your Spectrum Next, clone or image copy the SYNC command that is located in the above release zip file into your next dot folder.
Navigate to NextSync tab, select the root folder to sync on the left.
Once you have selected the folder hit the 'prepare sync' button, check the Next Sync log Window on the right.
First time you will run .sync on your will be prompter to select the <server> IP address, this machine running NextSync.
From the log window pick the IP address from this machine you want to use and type it on your next.
Then start the sync server on this maching using the Yes, start sync button and then run the .sync command on your Next.
At this point your Spectrum Next will connect to your machine using a network socket and the files will be sent to your next.
As it is your Next that will connect to this machine check your firewall alows inbound calls to this machine on port: 2048 by default." ),

The same syncignore.txt and syncpoint.dat file logic applies and alows you to control the sync (please check Jari documentation).

NextSync source code can be found here: https://github.com/jarikomppa/specnext/tree/master/sync

If you run in any type of issue using the NextSync integration please run first the Jari command line version to see if it works as expected.

* On Mac/Linux: you will also need to install manualy mono-complete package for example using: sudo apt-get install mono-complete

* Start ZX-Next-Unite.py

Mono (on Linux & MacOS Only)             
-----------------------------
Since CSpect is written in .NET you will also need to install manualy mono-complete package for example using: sudo apt-get install mono-complete 

GetIt
-----

The **GetIt** tab lets you browse and download software published for the ZX Spectrum Next directly from the official community archive.

- Website: https://www.specnext.com/getit/
- API base URL consumed by ZX-Next-Unite: `https://www.specnext.com/getit/`

You can search for files, page through the latest releases, and download titles directly to a folder of your choice. Once a file is downloaded you can transfer it to your HDF image or sync it to your Next using NextSync.

ZXDB
----

The **ZXDB** tab gives you access to the largest open-source database of ZX Spectrum and related software, maintained by the community.

- GitHub: https://github.com/zxdb/ZXDB
- API base URL consumed by ZX-Next-Unite: `https://api.zxdb.net/v1/`

You can search by title, browse entries, view screenshots and inlay scans, and download releases. The application queries the ZXDB REST API and downloads files directly from the URLs it returns — no files are hosted by this project.

zxArt
-----

The **zxArt** tab connects to the zxArt gallery and archive, which is dedicated to ZX Spectrum visual art, music, and productions (demos, games, graphics).

- Website: https://zxart.ee
- API base URL consumed by ZX-Next-Unite: `https://zxart.ee/api/`

You can search productions and pictures, preview artwork and screenshots, and download productions directly. All requests are made with the `ZX-Next-Unite` User-Agent identifier as required by the service.

Legal Disclaimer — Third-Party Content
---------------------------------------

> **The author of ZX-Next-Unite does NOT distribute any files, ROMs, games, demos, graphics, music, or any other content obtained through the GetIt, ZXDB, or zxArt integrations.**

All content is served exclusively by the respective third-party services listed above. ZX-Next-Unite acts solely as a frontend client that queries their public APIs and downloads files directly from the URLs those APIs return.

**It is the sole responsibility of the end user to ensure that any content they download or use through this application complies with the applicable copyright, licensing, and legal requirements in their jurisdiction.**

If in doubt, consult the terms of service of the relevant platform and seek appropriate legal advice before downloading or using any content.

The first time you open the GetIt, ZXDB, or zxArt pane, the application will display this disclaimer and ask you to acknowledge it. You can dismiss the dialog to continue using the pane; the dialog will reappear on the next visit until you check *"I agree and understand. Do not show this message again"*, at which point your acknowledgement is saved to `hdfg.cfg` and the dialog will not be shown again.

Settings
-----------
All the settings are retained in a signle file called: hdfg.cfg that will be automatically created in the same directory as the tool.
The file format is compatible with emOOK original tool but contains more options as this utility also provides NextSync support. In the case you'd like to reset all options simply backup and delete the existing hdfg.cfg file.

Known issues
----------------
* hdfmonkey external tool is required, when ZX-Next-Unite starts it will check for its presence in your system path. If not present on Windows it will try to automatically download it from the internet on Windows (MAC/Linux requires a manual installation). If for whatever reason hdfmonkey can't be downloaded (no internet access or python executable outbound internet calls are blocked by your machine firewall) you will need to download, extract & install manually CSpect in the same folder yourself. First time you start the tool if HdfmMonkey is not present, the log Windows will throw an error message reporting it is missing (as expected). Either download, extract & install manual hdfmonkey or use the Download and install button that will try to automate this for you. If ZX-Next-Unite fails to download HdfmMonkey and your internet connectivity works fine on Windows it is likely because the Windows firewall (or in your own antivirus firewall settings) prevents outbound call from you Python executable. To solve this on Windows you may for example download & extract it (a pre-compiled/ready to use version is available here: https://uto.speccy.org/downloads/hdfmonkey_windows.zip) from a seperate machine and then copy/transfer HdfmMonkey [dot] exe to the machine that will run ZX-Next-Unite (the safer option) or provide temporary access to the python [dot] exe file to the internet using the Windows Defender firewall control panel built in (with the security considerations that this implies).
* Pyside6 and recent version of Python are required to be installed first you may refer to the requirements section above.
* On other operating systems than Windows: you need to install, configure/compile manually hdfmonkey as well as mono (to be able to run Microsoft .NET on these platforms as Cspect is based on it).
* Some files on the Spectrum Next image that contain special characters such as single quotes may not be downloaded / retrieved correctly. 
* ESP/Wifi module errors may occur if connectivity is instable, if speed is too high you try reducing the flow by hitting the "Slow transfer" checkbox.
* Jari provided a great "setup & how" to guide in the nextsync.txt file located in the https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2  nextsync12.zip file. If you hit any setup issue with NextSync you may want to start by using his command line version of it (nextsync.py). Once nextsync12 is able to run and sync it should be the case as well of Next Sync in Py Hdfm Gooey since it based on the same code base as Jari python.

* When using NextSync the console may show some "Timer" messages, these may be safely ignored.


ZX-Next-Unite provides a cross platform (Windows, Linux, MacOS) Graphical Interface that unites two great tools in a single utility: Hdfm-Gooey & Next Sync. 
* The former is mostly targeted at CSpect emulator users, and it provides a simple to use frontend interface to transfer content to an image and start the Spectrum Next emulator. 
* The latter allows hardware owners such as Kickstarter & Clones that has a Wifi/ESP module to synchronize content directly from a machine over the network to their Next machines.

The first tab of ZX-Next-Unite is aimed at CSpect emulator users and developers. In that section you can mount an HDF image used by CSpect and upload download content using a built simple file explorer. Once the content is uploaded to the image you can then start CSpect directly from it and therefore exposes a simple to use frontend interface to the emulator. Key settings such as screen size can be directly adjusted, it avoids the need to know command line options to start using your emulated Spectrum Next.

The second tab is designed for real hardware owners such as KS1, KS2 or other clones that also has an ESP (WIFI module). Jari designed a while back a custom protocol called NextSync that allows to synchronize an entire folder from a machine (PC/MAC/Linux) that resides on a remote machine back to the Spectrum Next over the network (it is one way only). This utility implements the “server” side the program that will ‘listen’ and waits for incoming connections from your Next. On your Next machine you will run the custom dot “.sync” or ".syncfast" command that connects to the server here ZX-Next-Unite. To set this up you will need first to download Jari zip package. The latest release v1.2 of the dot .sync command can be found here https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 . 

Download Jari latest release and inside the zip dot folder you will find a file called “SYNC” and "SYNCFAST" (that allows faster transfer rates). Extract and copy that file in the /dot folder located on the root of your Spectrum Next SD card and boot it up as usual. Make sure WIFI is fully setup normally, that you can access the internet and your machine that will be running Py Hdfm gooey are on the same IP network and that no firewall blocks incoming communication on your “receiving” machine. By default, the custom protocol works on TCP port 2048.
Start, python ZX-Next-Unite.py and navigate to NextSync tab. On the built in Explorer select the root folder you’d like to sync and hit the “Prepare NextSync server” button. At this point the server is ready to go but not running yet, this allows you to review the above log. The first time I recommend that you select the “Sync once” option so the server will stop after the first .sync run (once .sync is working use .syncfast instead if possible). 
Since this is the first time you will run the .sync command on your Spectrum Next, the command will ask you to input what IP address it needs to connect to. In the log Window you should the machine Primary IP address (on non Windows system you may need to run ipconfig/ifconfig to see all available IPs). Your machine DHCP address usually starting by 10.0.x.y or 192.168.x.y, select the 4x digit IP address digit you are going to use for the upcoming step below.

On your Spectrum Next, select “Command Line” option and then simply type .sync and press enter. At this point as mentioned above it will request what IP address to connect to, so input the ip address selected above and press enter. 
On the next this configuration will be retained into C:/sys/config/nextsync.cfg if you need to change it later either edit the config file or type: .sync followed by the new IP address you wish to use to connect to for example: .sync 10.0.0.15 (it is the Next that will issue a connection to the PC/MAC/Linux).

That’s it you should now be all go to go.

When ready hit the “Yes, start NextSync server” button and on your next type “.sync” again. If everything is set up properly you should now see your next downloading all the files and once done the .sync command will exit. From now on you may use .syncfast instead for faster transfer rates if your network allows it.

There are many other option & features in the tool for example once the first full sync has been done it will create a SyncPoint file so it knows only what needs to be done and synced the next time you run the .sync command. If such a file exists, a button will appear that allows you to delete it in order that you can perform a full new sync if need be. A create SyncIgnore button will appear if such a file is not present. These file types allow you to control what file extensions will not be synced and send to the remote machine.

For more information, please check the “Help” tab in the tool or below.

Installation
------------
There is no need for an executable as the main ZX-Next-Unite.py file mostly needs a recent Python interpreter to run. 
Clone the repository, install a recent Python interpreter, install PySide6 Python library and you’re good to go. 

For a full install overview feel free to follow this "Setup & How to" video: https://youtu.be/FJG-Z0DCIjQ
  
Requirements
------------
- ZX-Next-Unite.py either download the latest release here or clone this repo, locate where this file is located that is main tool (there are no executable as it is a Python program).
- Python 3.7+ or higher (the latest version is recommended)
- PySide6
- CSpect emulator by Mike Dailly installed in local directory please download from http://www.cspect.org
    feel free to support his development efforts & patreon https://www.patreon.com/mikedailly
    - Make sure Spectrum Next roms installed are installed in local directory (they should be provided in the CSpect zip package by default). 
        These two files namely: enNextZX.rom and enNxtMMC.rom -MUST- be placed in the root folder of your #CSpect.
- You will need Spectrum Next images files that you can download from https://zxspectrumnext.online/cspect/  such as http://www.zxspectrumnext.online/cspect/cspect-next-2gb.zip
- Download & install hdfmonkey by Matt Westcott https://github.com/gasman/hdfmonkey , on Windows either compile the source manually or download a pre-compiled version at: 
    https://uto.speccy.org/downloads/hdfmonkey_windows.zip
- On Mac/Linux you will need to install mono-complete

* Additional help pages:
    - https://wiki.specnext.dev/Development_Tools:Linux_setup

* Install Python from: https://www.python.org/downloads/ (the latest version is recommended)
  
* Install pyside6 this is required for the UI to render the different controls being used. To install pyside6 open an elevated command shell and run:

    python -m pip install pyside6

    Note: On a regular basis you may want to update the pyside6 library running:  pip install --upgrade PySide6

* Copy Cspect (with the Spectrum Next roms) and hdfmonkey in the same directory (see above). 

start using it by running: 
    python ZX-Next-Unite.py
    
* On Windows download and install OpenAL sound library for CSpect: https://openal.org/

CSpect 
----------

CSpect emulator by Mike Dailly installed in local directory please download from http://www.cspect.org
feel free to support his development efforts & patreon https://www.patreon.com/mikedailly

Other Mike CSpect sites and links:
https://mdf200.itch.io/cspect
https://dailly.blogspot.com/

OpenAL sound library for CSpect: https://openal.org/
    
HDF Monkey 
----------
    
If you are running the app on Windows and hdfmonkey in not present in the same directory, you will see an error message in the main log Windows as it is missing.
If that is the case you will see a 'Download and Install button' bottom right, once clicked it will try to fetch from
https://uto.speccy.org/downloads/hdfmonkey_windows.zip and unzip hdfmonkey executable in the same directory. 
If the above automated install is successful, you should then be able to select an image and navigate it.
          
On Mac/Linux you will need to install hdfmonkey manually based on the instructions for your platform that can be found at: https://github.com/gasman/hdfmonkey
  
NextSync 
--------

Py Hdfm Gooey implements the <Server> side code and protocol of NextSync by Jari Komppa.
It does not require any dot .sync modification and it uses the same very close python logic as nextsync.py.
             
Initial realease on specnext: https://www.specnext.com/forum/viewtopic.php?f=17&t=1715&fbclid=IwAR1njrmr-wEU0DndAxBjO64K_NwY0E2zbqJVaVfiytHE2-A0eL8HWYeDKf8 
As a result you will need to run the dot same .sync command on your Next as with the console version and the same network protocol. 
             
The latest release v1.2 of the .sync command can be found here https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2 . 
             
You may follow the same instructions as the provided in the readme.txt of that release.
On your Spectrum Next, clone or image copy the SYNC command that is located in the above release zip file into your next dot folder.
Navigate to NextSync tab, select the root folder to sync on the left.
Once you have selected the folder hit the 'prepare sync' button, check the Next Sync log Window on the right.
First time you will run .sync on your will be prompter to select the <server> IP address, this machine running NextSync.
From the log window pick the IP address from this machine you want to use and type it on your next.
Then start the sync server on this maching using the Yes, start sync button and then run the .sync command on your Next.
At this point your Spectrum Next will connect to your machine using a network socket and the files will be sent to your next.
As it is your Next that will connect to this machine check your firewall alows inbound calls to this machine on port: 2048 by default." ),

The same syncignore.txt and syncpoint.dat file logic applies and alows you to control the sync (please check Jari documentation).

NextSync source code can be found here: https://github.com/jarikomppa/specnext/tree/master/sync

If you run in any type of issue using the NextSync integration please run first the Jari command line version to see if it works as expected.

* On Mac/Linux: you will also need to install manualy mono-complete package for example using: sudo apt-get install mono-complete
    
* Start ZX-Next-Unite.py

Mono (on Linux & MacOS Only)             
-----------------------------
Since CSpect is written in .NET you will also need to install manualy mono-complete package for example using: sudo apt-get install mono-complete 

Settings
-----------
All the settings are retained in a signle file called: hdfg.cfg that will be automatically created in the same directory as the tool.
The file format is compatible with emOOK original tool but contains more options as this utility also provides NextSync support. In the case you’d like to reset all options simply backup and delete the existing hdfg.cfg file.

Known issues
----------------
* hdfmonkey external tool is required, when ZX-Next-Unite starts it will check for its presence in your system path. If not present on Windows it will try to automatically download it from the internet on Windows (MAC/Linux requires a manual installation). If for whatever reason hdfmonkey can't be downloaded (no internet access or python executable outbound internet calls are blocked by your machine firewall) you will need to download, extract & install manually CSpect in the same folder yourself. First time you start the tool if HdfmMonkey is not present, the log Windows will throw an error message reporting it is missing (as expected). Either download, extract & install manual hdfmonkey or use the Download and install button that will try to automate this for you. If ZX-Next-Unite fails to download HdfmMonkey and your internet connectivity works fine on Windows it is likely because the Windows firewall (or in your own antivirus firewall settings) prevents outbound call from you Python executable. To solve this on Windows you may for example download & extract it (a pre-compiled/ready to use version is available here: https://uto.speccy.org/downloads/hdfmonkey_windows.zip) from a seperate machine and then copy/transfer HdfmMonkey [dot] exe to the machine that will run ZX-Next-Unite (the safer option) or provide temporary access to the python [dot] exe file to the internet using the Windows Defender firewall control panel built in (with the security considerations that this implies).
* Pyside6 and recent version of Python are required to be installed first you may refer to the requirements section above.
* On other operating systems than Windows: you need to install, configure/compile manually hdfmonkey as well as mono (to be able to run Microsoft .NET on these platforms as Cspect is based on it).
* Some files on the Spectrum Next image that contain special characters such as single quotes may not be downloaded / retrieved correctly. 
* ESP/Wifi module errors may occur if connectivity is instable, if speed is too high you try reducing the flow by hitting the “Slow transfer” checkbox.
* Jari provided a great “setup & how” to guide in the nextsync.txt file located in the https://github.com/Threetwosevensixseven/specnext/releases/tag/nextsync_v1.2  nextsync12.zip file. If you hit any setup issue with NextSync you may want to start by using his command line version of it (nextsync.py). Once nextsync12 is able to run and sync it should be the case as well of Next Sync in Py Hdfm Gooey since it based on the same code base as Jari python.

* When using NextSync the console may show some “Timer” messages, these may be safely ignored.





