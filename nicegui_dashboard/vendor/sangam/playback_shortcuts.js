// Playback owns Space only outside elements with their own keyboard behavior.
// Region and modal state are read at keydown, never captured at installation.
const nativeSelector = 'input,textarea,select,option,button,summary,a[href],[contenteditable]:not([contenteditable="false"]),[role="button"],[role="slider"],[role="combobox"],[role="menuitem"],[role="tab"],[role="switch"],[role="checkbox"],[role="radio"],[role="textbox"],[role="spinbutton"]';
const bindingKey = '__uavPlaybackShortcuts';

export function ownsSpaceInput(target){
    return !!target?.closest?.(nativeSelector);
}

export function focusPlaybackSurface(event,surface,enabled=true){
    if(!enabled||!surface||ownsSpaceInput(event?.target))return false;
    surface.tabIndex=-1;
    surface.focus({preventScroll:true});
    return true;
}

export function installPlaybackShortcuts(options){
    const doc=options.document||document;
    doc[bindingKey]?.dispose();
    let held=false,handled=false;
    const isSpace=e=>e.code==='Space'||e.key===' '||e.key==='Spacebar';
    const keydown=e=>{
        if(!isSpace(e))return;
        if(held||e.repeat){if(handled)e.preventDefault();return;}
        if(e.defaultPrevented||e.isComposing||e.ctrlKey||e.altKey||e.metaKey||e.shiftKey||
           ownsSpaceInput(e.target)||!options.enabled()||options.blocked?.())return;
        const action=options.incidentActive()?options.toggleIncident:
            options.mainActive()?options.toggleMain:null;
        if(!action||action()===false)return;
        held=true;handled=true;e.preventDefault();
    };
    const keyup=e=>{
        if(!isSpace(e))return;
        if(handled)e.preventDefault();
        held=false;handled=false;
    };
    const release=()=>{held=false;handled=false;};
    doc.addEventListener('keydown',keydown);
    doc.addEventListener('keyup',keyup);
    doc.defaultView?.addEventListener('blur',release);
    const binding={dispose(){
        doc.removeEventListener('keydown',keydown);
        doc.removeEventListener('keyup',keyup);
        doc.defaultView?.removeEventListener('blur',release);
        release();
        if(doc[bindingKey]===binding)delete doc[bindingKey];
    }};
    doc[bindingKey]=binding;
    return binding;
}
