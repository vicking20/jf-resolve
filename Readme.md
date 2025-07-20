jf-resolve acts as a bridge between real debrid and jellyfin. It connects to jellyfin, jellyseerr, radarr, sonarr, prowlarr and populates your library with some movies and tv shows through jellyseerr.
Jf-resolve does not store direct files to your machine, instead it connects to your real debrid account and populates your library with streamable links from real debrid, all requests made from jellyseerr also will be automatically be added to your library without downloading the whole file but is playable from the moment jellyfin adds it to your library.

##On Initialization

#**Jellyfin**
#Before
![Jellyfin initial Library](images/libinit.png)

#**Radarr**
#Before
![radarr initial size](images/initmoviesize.png)
![radarr initial list](images/initmovielist.png)

#**Sonarr**
#Before
![sonarr initial size](images/inittvsize.png)
![sonarr initial list](images/inittvlist.png)

#**pros**
- Slightly less wait time, items can be played almost immediately once they appear on jellyfin

- Space savings, 14 movie requests made by jellyseerr, the total size of my movies library came out to be 248kb considering youre just streaming instead of having the files locally

#**cons**
- Subtitles may, or may not work depending on the media you get. Using open subtitles plugin may help fix this

- Media that need transcoding will be transcoded each time you play them, the file isnt hosted locally, so thats to be expected

- Not fully compatible with an already setup arr stack

#**stack used:**
- Jellyfin
- Jellyseerr
- Radarr
- Sonarr
- Prowlarr

Note: Although you can set the jellyseerr aggression to a high number, its probably better to keep it low, below 5 if possible, because high numbers can mean youre scraping tons of items you may not need, requests made by jellyseerr manually are still processed as usual, the library will grow eventually.

#**High level overview**
The whole system is python based, python, pip and docker are compulsory for the setup to work. You also need to have a real debrid subscription and an api key for this to work and the installer guides you on the necessary steps needed.

The controller calls your jellyseerr backend to query for movies and tv shows based on how aggresive you want it to be, then it requests for those from jellyseerr and jellyseerr sends them either to radarr or prowlarr, the torrent/magnet files are loaded, controller watchdog will pick that up and make a request to your real debrid account from your api key, after that, a link is generated for you that is appeneded to a streamable file and added to your jellyfin library, the watchdog at the moment refreshes those links, I've heard that these links expire... So ive set it to refresh them after 30 days, I really don't know if 30 days is too long a time however, till someone can confirm what the actual duration is.

Movies are sorted to movies, tv series are sorted and updated also updated along with sonarr when new episodes are added/updated, quality of your results will vary based on the quality of your indexers.

Media quality depends on your default resolution set in jellyseerr, if you set 1080p as default, then jellyseerr will request 1080p media files, you can probably change that in jellyseerr settings anytime you wish, especially if file sizes are too large, Ive had some 1080p movies be as large as 16gb in size... For local media, transcodding would be very useful here since it can help greatly reduce file sizes, Im not sure jf-resolve will ever be able to fix that.

In addition, if you arent using jellyfin themes, but want to customize your web client, you can check this out https://github.com/lscambo13/ElegantFin/releases

All credit goes to the owners of the original project, all teams in charge of jellyfin, jellyseerr, radarr, sonarr, prowlarr... without them, it wont be possible, in a former version of this, I wrote each module myself, and i promise that it is extremly difficult to hunt for the right torrents especially for tvseries.. or im just bad at coding.
I also give credit to automation avenue as his arr compose file was what i used for this, his youtube video was good for setting the ball rolling.

To setup jf-resolve, clone/download the project, go to the folder and run installer.py, it should do most of the setup for you from the ground up and run the controller.
If not, you can follow a manual setup below

#**manual setup (not needed if you are running with installer.py)**
-> open the example.env file and update the path where you want to set things up, this is the folder where all media and container volumes will be mapped to, rename this file to .env when completed
-> go to real-debrid, get your api key and paste into the line for the real debrid api key
-> check the compose file and see if you need to modify to your liking, if not, run the docker compose up -d command and get your setup running
-> the path you have setup, for linux and macs, you may need to change owner as they may be created with the root user as owner, then the containers cant access the volume binds, one fix is to manually create the folders as structured in the env file, then docker wont recreate them, if not, you can run the chown command to change owner from root to your user id and group id.
-> after bringing the containers up, configure your stack tools individually, some instruction on how to do it can be found in the instructions.txt file in this project. after completing step6, you can now paste the api key for your jellyseerr instance into the .env file where the line for jellyseerr api key is, then save and close your env file.
-> open the example.config.ini file and configure as desired, there are hints to what some of the options do, save as config.ini and close when done.
-> in your terminal, run "python controller.py --initiate" and you should be set, for linux/unix, you can add nohup before running your command if you want it in the background.

This is all for educational purposes... I was testing to see if you could programmatically make jellyfin play media that does not exist locally on your machine and complicate the process by adding a debrid service and also tying it in with the common arr stack. It was fun, I think its okay, and others can modify to their liking or use for educational purposes too. Jf-resole was really made for linux machines, I do not promise that this will work fine on other platforms, precisely windows, but its worth a try. Im not able to test it for errors too, and it probably has a couple of those, if and when I can, I can check those out and fix the errors.

If you dont find your media populated on jellyfin, rescan your library...

In addition, you can make it easier on your server by going to radarr and sonarr, changin the profile or reducing them, if for example you have a remux, which could be 20gb for one movie, youll probably be stuck in a spinning loop for a long time, unfortunately, I cant fix that. Here is a guide explaining how to setup quality profiles: [Trashguide](https://trash-guides.info/Radarr/radarr-setup-quality-profiles/) 


