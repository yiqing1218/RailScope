"""Space outlet labels without altering their ports or rail geometry."""


def place_port_labels(ports, options):
    boxes = []
    size = options.label_size * .8
    for port in sorted(ports,key=lambda p:(p['side'],p['point'][1],p['key'])):
        if not port['visible']:
            continue
        px,py = port['point']
        side = port['side']
        x = px-16 if side=='left' else px+16 if side=='right' else px
        y = py+32 if side=='bottom' else py-18
        rule = options.port_overrides.get(port['key'], {})
        width = size * 9
        left = x-width if side=='left' else x if side=='right' else x-width/2
        if not any(k in rule for k in ('dx','dy','text')):
            for _ in range(len(ports)+1):
                if not any(left < r and left+width > l and y-size < b and y > t for l,t,r,b in boxes):
                    break
                y += -size*1.3 if side=='top' else size*1.3
        port['label_point'] = (x,y)
        boxes.append((left,y-size,left+width,y+size*.2))
