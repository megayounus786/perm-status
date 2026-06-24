#!/usr/bin/env python3
"""Insert the 'collapse empty Monumetric ad slots' guard into every page that
loads the Monumetric loader. Idempotent (keyed on the marker id) and backs up
each edited file to backups/ before writing.

Diagnosis: Monumetric injects `_zone` (and sticky `_placeholder`) divs carrying
an INLINE `min-height:Npx !important`. While the ad is unfilled the inner
google_ads iframe is 0px, so that inline min-height is the ONLY thing reserving
vertical space -> blank gaps. Inline !important cannot be beaten by a stylesheet,
so we override the inline property with JS and reveal the slot again as soon as a
real ad iframe (h>1,w>1) appears.
"""
import glob
import os
import time

MARKER = "mmt-empty-collapse"
LOADER = '<script type="text/javascript" src="//monu.delivery/site/6/7/24ec8e-ce2f-4a85-ba1a-e1a4ecaea496.js" data-cfasync="false"></script>'

BLOCK = '''
<!-- ImmiLane: collapse EMPTY Monumetric ad slots until a real ad fills them (added 2026-06-24). Removes blank reserved gaps while ads are unfilled; reveals each slot at its reserved size the moment a creative loads. Does not touch the loader or ad-unit ids. -->
<style id="mmt-empty-collapse-css">div[id^="mmt-"]:not([data-mmt-filled]){min-height:0}</style>
<script id="mmt-empty-collapse">
(function(){
  var SEL='div[id^="mmt-"][id$="_zone"], div[id^="mmt-"][id$="_placeholder"]';
  function realAd(z){
    var f=z.getElementsByTagName('iframe');
    for(var i=0;i<f.length;i++){var r=f[i].getBoundingClientRect();if(r.height>1&&r.width>1)return true;}
    return false;
  }
  function sweep(){
    var z=document.querySelectorAll(SEL);
    for(var i=0;i<z.length;i++){
      var el=z[i];
      if(el.getAttribute('data-mmt-filled')==='1')continue;
      if(realAd(el)){
        el.setAttribute('data-mmt-filled','1');
        var o=el.getAttribute('data-mmt-orig-mh');
        if(o)el.style.setProperty('min-height',o,'important');
        else el.style.removeProperty('min-height');
        continue;
      }
      if(el.getAttribute('data-mmt-collapsed')!=='1'){
        el.setAttribute('data-mmt-orig-mh',el.style.getPropertyValue('min-height'));
        el.setAttribute('data-mmt-collapsed','1');
      }
      var cur=el.style.getPropertyValue('min-height');
      if(cur!=='0px'&&cur!=='0')el.style.setProperty('min-height','0','important');
    }
  }
  function start(){
    sweep();
    try{new MutationObserver(sweep).observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['style','id']});}catch(e){}
    var n=0,iv=setInterval(function(){sweep();if(++n>120)clearInterval(iv);},1000);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);
  else start();
})();
</script>'''

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)
    bdir = os.path.join("backups", "mmt_collapse_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(bdir, exist_ok=True)
    edited, skipped = [], []
    for path in sorted(glob.glob("*.html")):
        with open(path, "r", encoding="utf-8") as fh:
            html = fh.read()
        if LOADER not in html:
            skipped.append((path, "no loader"))
            continue
        if MARKER in html:
            skipped.append((path, "already has guard"))
            continue
        with open(os.path.join(bdir, path + ".bak"), "w", encoding="utf-8") as bh:
            bh.write(html)
        new = html.replace(LOADER, LOADER + BLOCK, 1)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
        edited.append(path)
    print("backup dir:", bdir)
    print("edited (%d):" % len(edited))
    for p in edited:
        print("  ", p)
    print("skipped (%d):" % len(skipped))
    for p, why in skipped:
        print("  ", p, "->", why)

if __name__ == "__main__":
    main()
