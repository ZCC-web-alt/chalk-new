"use client"

import { useMemo } from "react"
import { Bounds, Line, OrbitControls } from "@react-three/drei"
import { Canvas } from "@react-three/fiber"
import type { StructureGeometry } from "@/lib/api/types"


const ELEMENT_COLORS: Record<string, string> = {
  H: "#f5f5f5", C: "#444444", N: "#2563eb", O: "#dc2626", F: "#16a34a",
  P: "#ea580c", S: "#eab308", Cl: "#22c55e", Fe: "#b45309", Co: "#7c3aed",
  Ni: "#059669", Cu: "#c2410c", Zn: "#64748b", Si: "#d97706", Li: "#ec4899",
}

export function StructureViewer({ geometry }: { geometry: StructureGeometry }) {
  const center = useMemo(() => {
    if (!geometry.atoms.length) return [0, 0, 0] as [number, number, number]
    const values = geometry.atoms.map((atom) => atom.cartesian)
    const mins = [0, 1, 2].map((axis) => Math.min(...values.map((value) => value[axis])))
    const maxs = [0, 1, 2].map((axis) => Math.max(...values.map((value) => value[axis])))
    return [0, 1, 2].map((axis) => (mins[axis] + maxs[axis]) / 2) as [number, number, number]
  }, [geometry])

  const cellLines = useMemo(() => {
    const [a, b, c] = geometry.cell
    if (!a || !b || !c) return []
    const add = (left: number[], right: number[]) => left.map((value, index) => value + right[index])
    const origin = [0, 0, 0]
    const ab = add(a, b)
    const ac = add(a, c)
    const bc = add(b, c)
    const abc = add(ab, c)
    return [
      [origin, a], [origin, b], [origin, c], [a, ab], [a, ac], [b, ab], [b, bc],
      [c, ac], [c, bc], [ab, abc], [ac, abc], [bc, abc],
    ].map(([from, to]) => [
      from.map((value, index) => value - center[index]),
      to.map((value, index) => value - center[index]),
    ] as [[number, number, number], [number, number, number]])
  }, [center, geometry.cell])

  return (
    <div className="flex h-full min-h-80 w-full flex-col bg-[#f7f9fb]" data-testid="structure-viewer">
      <div className="flex min-h-9 flex-wrap items-center gap-3 border-b border-border bg-card px-3 py-1 text-xs text-muted-foreground">
        <strong className="text-foreground">{geometry.formula || "Structure"}</strong>
        <span>{geometry.atoms.length.toLocaleString()} atoms</span>
        <span>{geometry.lattice.volume?.toFixed?.(2) ?? geometry.lattice.volume} A3</span>
        {(geometry.bondsOmitted || geometry.bondsTruncated) && <span className="text-warning">Bond preview limited</span>}
      </div>
      <div className="min-h-0 flex-1">
        <Canvas
          data-testid="structure-canvas"
          camera={{ position: [8, 8, 8], fov: 42, near: 0.1, far: 1000 }}
          gl={{ antialias: true, preserveDrawingBuffer: true }}
        >
          <color attach="background" args={["#f7f9fb"]} />
          <ambientLight intensity={1.25} />
          <directionalLight position={[7, 10, 8]} intensity={2.4} />
          <directionalLight position={[-6, -4, -5]} intensity={0.8} />
          <OrbitControls makeDefault enableDamping dampingFactor={0.08} />
          <Bounds fit clip observe margin={1.2}>
            <group>
              {cellLines.map((points, index) => (
                <Line key={`cell-${index}`} points={points} color="#64748b" lineWidth={1} dashed dashSize={0.12} gapSize={0.08} />
              ))}
              {geometry.bonds.map((bond, index) => {
                const from = geometry.atoms[bond.from]?.cartesian
                const to = geometry.atoms[bond.to]?.cartesian
                if (!from || !to) return null
                return (
                  <Line
                    key={`bond-${index}`}
                    points={[
                      from.map((value, axis) => value - center[axis]) as [number, number, number],
                      to.map((value, axis) => value - center[axis]) as [number, number, number],
                    ]}
                    color="#94a3b8"
                    lineWidth={2}
                  />
                )
              })}
              {geometry.atoms.map((atom) => (
                <mesh
                  key={atom.index}
                  position={atom.cartesian.map((value, axis) => value - center[axis]) as [number, number, number]}
                >
                  <sphereGeometry args={[atom.element === "H" ? 0.22 : 0.36, 32, 24]} />
                  <meshStandardMaterial color={ELEMENT_COLORS[atom.element] ?? "#0f766e"} roughness={0.38} metalness={0.08} />
                </mesh>
              ))}
            </group>
          </Bounds>
        </Canvas>
      </div>
    </div>
  )
}
