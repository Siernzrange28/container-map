// Thread constellations on a far shell: two buffers, one instanced working hull, six labels.
(function(g){
  'use strict';
  var C=g.CM3D, S=null, K={};
  K.layout=function(w,limit){
    var out=[], count=0, root=w.star||{x:0,y:0};
    (w.constellations||[]).forEach(function(lane,i){
      var points=[], angle=lane.angle==null?-Math.PI/2+i*Math.PI/3:lane.angle;
      (lane.threads||[]).forEach(function(t){
        if(count>=(limit||2048)) return;
        var dx=t.x-root.x, dz=t.y-root.y, a=isFinite(t.angle)?t.angle:angle;
        var r=Math.max(3000,Math.hypot(dx||0,dz||0)*1.8);
        points.push({thread:t,pos:[root.x+Math.cos(a)*r,300*Math.sin(i*Math.PI/3),root.y+Math.sin(a)*r]}); count++;
      });
      out.push({lane:lane,angle:angle,points:points});
    }); return out;
  };
  C.constellationKit=K;
  function dispose(){
    if(!S) return;
    S.scene.remove(S.root);
    S.root.traverse(function(o){ if(o.geometry) o.geometry.dispose(); if(o.material) o.material.dispose(); });
    S.labels.forEach(function(l){ if(l.el.parentNode) l.el.parentNode.removeChild(l.el); });
    if(S.canvas) S.canvas.removeEventListener('click',S.click);
    S=null;
  }
  function build(scene,w,ctx){
    dispose(); var T=ctx.THREE, root=new T.Group(), pos=[], rgb=[], sizes=[], lines=[], points=[], groups=K.layout(w,ctx.quality.phone?768:2048);
    S={scene:scene,root:root,labels:[],points:points,ctx:ctx}; scene.add(root);
    groups.forEach(function(group){
      group.points.forEach(function(p,j){
        var t=p.thread, c=new T.Color(t.colour||'#64748B'), b=t.brightness==null?1:t.brightness;
        pos.push.apply(pos,p.pos); rgb.push(c.r*b,c.g*b,c.b*b); sizes.push(2*(t.radius||4)); points.push(p);
        if(j) lines.push.apply(lines,group.points[j-1].pos.concat(p.pos));
      });
      if(g.document){ var el=g.document.createElement('div'); el.className='thread-lane3d';
        el.style.cssText='position:fixed;pointer-events:none;color:#94A3B8;font:13px "Barlow Condensed",sans-serif;z-index:12;transform:translate(-50%,-50%)';
        var open=group.points.filter(function(p){ return !/^(settled|done)$/.test(p.thread.state); }).length;
        el.textContent=group.lane.label+' · '+open+' open'; g.document.body.appendChild(el);
        S.labels.push({el:el,pos:[(w.star.x||0)+Math.cos(group.angle)*3400,340*Math.sin(groups.indexOf(group)*Math.PI/3),(w.star.y||0)+Math.sin(group.angle)*3400]}); }
    });
    var geo=new T.BufferGeometry(); geo.setAttribute('position',new T.Float32BufferAttribute(pos,3)); geo.setAttribute('color',new T.Float32BufferAttribute(rgb,3)); geo.setAttribute('size',new T.Float32BufferAttribute(sizes,1));
    var mat=new T.ShaderMaterial({vertexShader:'attribute vec3 color; attribute float size; varying vec3 vC; void main(){vC=color; gl_PointSize=size; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
      fragmentShader:'varying vec3 vC; void main(){if(length(gl_PointCoord-0.5)>0.5) discard; gl_FragColor=vec4(vC,1.0);}',depthWrite:false});
    root.add(new T.Points(geo,mat));
    var lg=new T.BufferGeometry(); lg.setAttribute('position',new T.Float32BufferAttribute(lines,3));
    root.add(new T.LineSegments(lg,new T.LineBasicMaterial({color:'#38BDF8',transparent:true,opacity:.45,depthWrite:false})));
    var working=points.filter(function(p){ return p.thread.state==='working'&&p.thread.running; }).slice(0,ctx.quality.phone?16:64);
    if(working.length&&C.hull){
      var models=((ctx.assets&&ctx.assets.skin3d)||ctx.skin3d).models3d||{}, recipe=models.wingman||models.own;
      if(recipe){ var built=C.hull.flightBuilt(C.hull.build(recipe,{models:models,lod:1})), hg=C.hull.toThree(T,built,{}), hm=new T.MeshBasicMaterial({vertexColors:true});
        var mesh=new T.InstancedMesh(hg,hm,working.length), obj=new T.Object3D();
        working.forEach(function(p,i){ obj.position.set(p.pos[0]+22,p.pos[1]+12,p.pos[2]); obj.rotation.y=-p.thread.angle-Math.PI/2; obj.updateMatrix(); mesh.setMatrixAt(i,obj.matrix); });
        root.add(mesh); }
    }
    S.canvas=ctx.renderer.domElement;
    S.click=function(e){ if(!S) return; var rect=S.canvas.getBoundingClientRect(), x=e.clientX-rect.left,y=e.clientY-rect.top, best=null,dist=14;
      S.points.forEach(function(p){ var q=ctx.project.apply(null,p.pos), d=q&&q.onScreen?Math.hypot(q.x-x,q.y-y):Infinity; if(d<dist){dist=d;best=p;} });
      if(best&&ctx.page.openThread) ctx.page.openThread(best.thread);
    };
    if(S.canvas) S.canvas.addEventListener('click',S.click);
  }
  C.module('routes',{build:build,rebuild:build,update:function(w,dt,ctx){ if(!S) return;
    S.labels.forEach(function(l){var q=ctx.project.apply(null,l.pos); l.el.hidden=!q||!q.onScreen; if(q){l.el.style.left=q.x+'px';l.el.style.top=q.y+'px';}});
    if(ctx.audit) ctx.audit.scene.constellations={stars:S.points.length,labels:S.labels.length,drawCalls:3};
  },dispose:dispose});
})(globalThis);
