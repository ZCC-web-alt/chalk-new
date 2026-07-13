"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { Image as KonvaImage, Layer, Rect, Stage } from "react-konva"
import type { KonvaEventObject } from "konva/lib/Node"
import { Maximize2, ZoomIn, ZoomOut } from "lucide-react"
import type { MultimodalAnnotation } from "@/lib/api/types"
import { IconBtn } from "../ui"


type Point = { x: number; y: number }

export function MultimodalAnnotationCanvas({
  imageUrl,
  annotations,
  onCreate,
  selectedId,
  onSelect,
}: {
  imageUrl: string
  annotations: MultimodalAnnotation[]
  onCreate: (annotation: MultimodalAnnotation) => void
  selectedId: string | null
  onSelect: (id: string | null) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 640, height: 480 })
  const [image, setImage] = useState<HTMLImageElement | null>(null)
  const [zoom, setZoom] = useState(1)
  const [position, setPosition] = useState<Point>({ x: 0, y: 0 })
  const [start, setStart] = useState<Point | null>(null)
  const [draft, setDraft] = useState<MultimodalAnnotation | null>(null)

  useEffect(() => {
    const element = containerRef.current
    if (!element) return
    const update = () => setSize({
      width: Math.max(280, element.clientWidth),
      height: Math.max(280, element.clientHeight),
    })
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const next = new window.Image()
    next.crossOrigin = "use-credentials"
    next.onload = () => setImage(next)
    next.src = imageUrl
    return () => {
      next.onload = null
    }
  }, [imageUrl])

  const frame = useMemo(() => {
    if (!image) return { x: 0, y: 0, width: size.width, height: size.height }
    const scale = Math.min(size.width / image.naturalWidth, size.height / image.naturalHeight)
    const width = image.naturalWidth * scale
    const height = image.naturalHeight * scale
    return { x: (size.width - width) / 2, y: (size.height - height) / 2, width, height }
  }, [image, size])

  const toNormalized = (point: Point) => {
    const x = (point.x - frame.x) / frame.width
    const y = (point.y - frame.y) / frame.height
    if (x < 0 || x > 1 || y < 0 || y > 1) return null
    return { x, y }
  }

  const logicalPointer = (event: KonvaEventObject<MouseEvent>) => {
    const pointer = event.target.getStage()?.getPointerPosition()
    if (!pointer) return null
    return { x: (pointer.x - position.x) / zoom, y: (pointer.y - position.y) / zoom }
  }

  const handleDown = (event: KonvaEventObject<MouseEvent>) => {
    if (event.evt.button !== 0) return
    const point = logicalPointer(event)
    if (!point || !toNormalized(point)) return
    setStart(point)
    setDraft(null)
    onSelect(null)
  }

  const handleMove = (event: KonvaEventObject<MouseEvent>) => {
    if (!start) return
    const point = logicalPointer(event)
    if (!point) return
    const first = toNormalized(start)
    const second = toNormalized({
      x: Math.min(frame.x + frame.width, Math.max(frame.x, point.x)),
      y: Math.min(frame.y + frame.height, Math.max(frame.y, point.y)),
    })
    if (!first || !second) return
    setDraft({
      id: "draft",
      x: Math.min(first.x, second.x),
      y: Math.min(first.y, second.y),
      width: Math.abs(first.x - second.x),
      height: Math.abs(first.y - second.y),
      note: "",
    })
  }

  const handleUp = () => {
    setStart(null)
    if (draft && draft.width > 0.01 && draft.height > 0.01) {
      const annotation = { ...draft, id: crypto.randomUUID() }
      onCreate(annotation)
      onSelect(annotation.id)
    }
    setDraft(null)
  }

  const handleWheel = (event: KonvaEventObject<WheelEvent>) => {
    event.evt.preventDefault()
    const stage = event.target.getStage()
    const pointer = stage?.getPointerPosition()
    if (!stage || !pointer) return
    const previous = zoom
    const next = Math.min(4, Math.max(0.75, previous * (event.evt.deltaY > 0 ? 0.9 : 1.1)))
    const anchor = { x: (pointer.x - position.x) / previous, y: (pointer.y - position.y) / previous }
    setZoom(next)
    setPosition({ x: pointer.x - anchor.x * next, y: pointer.y - anchor.y * next })
  }

  const reset = () => {
    setZoom(1)
    setPosition({ x: 0, y: 0 })
  }

  const rectangles = draft ? [...annotations, draft] : annotations

  return (
    <div ref={containerRef} className="relative h-full min-h-72 w-full overflow-hidden bg-secondary/40">
      <div className="absolute right-2 top-2 z-10 flex items-center gap-1 rounded-md border border-border bg-card/95 p-1 shadow-sm">
        <IconBtn icon={ZoomOut} aria-label="缩小" title="缩小" onClick={() => setZoom((value) => Math.max(0.75, value - 0.25))} />
        <span className="w-10 text-center text-[11px] tabular-nums text-muted-foreground">{Math.round(zoom * 100)}%</span>
        <IconBtn icon={ZoomIn} aria-label="放大" title="放大" onClick={() => setZoom((value) => Math.min(4, value + 0.25))} />
        <IconBtn icon={Maximize2} aria-label="重置视图" title="重置视图" onClick={reset} />
      </div>
      <Stage
        width={size.width}
        height={size.height}
        scaleX={zoom}
        scaleY={zoom}
        x={position.x}
        y={position.y}
        draggable={!start && zoom > 1}
        onDragEnd={(event) => setPosition({ x: event.target.x(), y: event.target.y() })}
        onMouseDown={handleDown}
        onMouseMove={handleMove}
        onMouseUp={handleUp}
        onMouseLeave={handleUp}
        onWheel={handleWheel}
      >
        <Layer>
          {image && <KonvaImage image={image} {...frame} />}
          {rectangles.map((annotation) => (
            <Rect
              key={annotation.id}
              x={frame.x + annotation.x * frame.width}
              y={frame.y + annotation.y * frame.height}
              width={annotation.width * frame.width}
              height={annotation.height * frame.height}
              stroke={annotation.id === selectedId ? "#1677ff" : "#d48806"}
              strokeWidth={annotation.id === selectedId ? 3 / zoom : 2 / zoom}
              fill={annotation.id === selectedId ? "rgba(22,119,255,0.12)" : "rgba(212,136,6,0.1)"}
              onClick={(event) => {
                event.cancelBubble = true
                onSelect(annotation.id === "draft" ? null : annotation.id)
              }}
            />
          ))}
        </Layer>
      </Stage>
    </div>
  )
}
