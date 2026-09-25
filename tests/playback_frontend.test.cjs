// Run with Node.js: node tests/playback_frontend.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const blobs = new Map();
let blobIndex = 0;
const context = {
  console, setTimeout, clearTimeout, setInterval, clearInterval, Blob, URLSearchParams,
  URL: {createObjectURL: blob => {const url=`blob:${++blobIndex}`;blobs.set(url,blob);return url;}, revokeObjectURL: url=>blobs.delete(url)},
  navigator: {}, document: {fullscreenElement:null,addEventListener(){},removeEventListener(){}},
  window: {}, fetch: null,
  Alpine: {store: name => name==='ui' ? ui : {error(){}}},
};
const ui={playerOpen:true};
vm.createContext(context);
vm.runInContext(fs.readFileSync('experimental/frontend/static/js/pages/player.js','utf8')+'\nthis.Player = VideoPlayer;',context);
vm.runInContext(fs.readFileSync('experimental/frontend/static/js/pages/media_detail.js','utf8'),context);
(async()=>{
  const player=new context.Player('test');let loaded;
  player._initArtPlayer=(...args)=>loaded=args;
  context.fetch=async()=>({ok:true,headers:{get:()=> 'video/mp4'},body:{cancel:async()=>{}}});
  await player.load('/source');assert.equal(loaded[0],'/source');assert.equal(loaded[1],false);
  context.fetch=async()=>({ok:false,status:503,json:async()=>({detail:'Queue full'})});
  await assert.rejects(()=>player.load('/source'),/Queue full/);

  player.streamId='session';player.streamOffset=18;player.duration=90;
  const video=new EventTarget();video.currentTime=1;video.paused=false;
  let played=0,subtitleUrl,events=[];
  player.art={video,pause(){video.paused=true;},play:async()=>{played++;video.paused=false;},
    notice:{show:''},subtitle:{switch:async url=>{subtitleUrl=url;}},
    set url(value){video.currentTime=0;setTimeout(()=>video.dispatchEvent(new Event('canplay')),0);}};
  await player.setSubtitleText('1\n00:00:01,000 --> 00:00:03,000\nOld\n\n2\n00:00:19,000 --> 00:00:21,000\n<i>Late</i>');
  let vtt=await blobs.get(subtitleUrl).text();assert(!vtt.includes('Old'));assert(!vtt.includes('<i>'));assert(vtt.includes('Late'));assert(vtt.includes('00:00:01.000 --> 00:00:03.000'));
  context.fetch=async()=>({ok:true,json:async()=>({min_time:18,max_time:24,segment_count:2})});
  await player.seek(20);assert.equal(video.currentTime,2);assert.equal(player.currentTime,20);
  player.hls={destroy(){events.push('destroy');}};
  context.fetch=async url=>{events.push(url.includes('/seek?')?'seek':'range');return {ok:true,json:async()=>({min_time:18,max_time:24,segment_count:2})};};
  await player.seek(3);assert.equal(player.streamOffset,3);assert.equal(video.currentTime,0);assert.equal(played,1);
  assert(events.indexOf('destroy')<events.indexOf('seek'));assert.equal(player.isUserSeeking,false);assert.equal(player.isInternalSeeking,false);
  context.fetch=async url=>({ok:!url.includes('/seek?'),json:async()=>url.includes('/seek?')?{detail:'Seek failed'}:{min_time:3,max_time:9,segment_count:2}});
  await assert.rejects(()=>player.seek(40),/Seek failed/);assert.equal(player.isUserSeeking,false);assert.equal(player.isInternalSeeking,false);

  // Emulate Alpine wrapping assigned objects. Its stored player reference must
  // remain the owner even when the previous player emits a close event.
  class FakePlayer {
    constructor(){this.hooks={};this.streamId='new-session';this.totalDuration=90;this.currentTime=0;}
    on(name,fn){this.hooks[name]=fn;}
    destroy(){this.hooks.onClose?.();}
    async load(){}
  }
  context.FakePlayer=FakePlayer;
  vm.runInContext("VideoPlayer = FakePlayer", context);
  const raw=context.mediaDetailPage(null,false),proxies=new WeakMap();
  const page=new Proxy(raw,{get(target,key){const value=target[key];if(key!=='videoPlayer'||!value)return value;if(!proxies.has(value))proxies.set(value,new Proxy(value,{}));return proxies.get(value);}});
  page.stopProgressTracking=()=>{};
  context.fetch=async()=>({ok:true});
  await page.initPlayer('/first');const previous=page.videoPlayer;
  await page.initPlayer('/second');assert.notEqual(page.videoPlayer,previous);assert.equal(page.currentStreamId,'new-session');assert.equal(ui.playerOpen,true);
  let selected;
  page.allSources=[{type:'stream',data:{url:'first'}},{type:'stream',data:{url:'second'}}];
  page.playSourceByIndex=async index=>selected=index;
  await page.playStream({url:'first'});assert.equal(selected,0);
  await page.playStream({url:'second'});assert.equal(selected,1);
  const remotePage=context.mediaDetailPage(null,false);
  remotePage.videoPlayer={};
  context.fetch=async()=>({ok:true,json:async()=>({audio_tracks:[],subtitle_tracks:[{index:0,language:'eng',supported:true}]})});
  await remotePage.fetchAudioTracksFromUrl('https://example.com/video.mkv');
  assert.equal(remotePage.subtitleTracks[0].language,'eng');
  const heartbeatPlayer=new context.Player('test');
  heartbeatPlayer.streamId='session';
  let ping,stopped=false;
  context.setInterval=fn=>{ping=fn;return 123;};
  context.clearInterval=id=>{if(id===123)stopped=true;};
  context.fetch=async()=>({status:200});
  heartbeatPlayer.startHeartbeat();await ping();
  heartbeatPlayer.destroy();assert(stopped);
  console.log('Passed: direct loading, startup errors, subtitle timing, native/restarted seeks, failed seek cleanup, Alpine ownership, source labels, remote MKV subtitles, heartbeat cleanup, live subtitle timing and download selection');
})().catch(error=>{console.error(error);process.exitCode=1;});
