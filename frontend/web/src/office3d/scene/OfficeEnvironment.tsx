// The office's environment light (T-412, replaces drei <Environment><Lightformer/></Environment>):
// three light panels (above, and on the two open sides) baked once into an environment map with
// PMREMGenerator. drei's version renders its children through an R3F portal, and that portal
// keeps every previous root state alive (each state's setEvents closure captures the one before):
// in a 2-hour soak that was a steady ~2.5 MB/h of heap growth. Baked once, it costs nothing per
// frame and holds nothing.
import { useThree } from "@react-three/fiber";
import { useEffect } from "react";
import { Color, DoubleSide, Mesh, MeshBasicMaterial, PlaneGeometry, PMREMGenerator, Scene } from "three";

/** [width, height, colour, intensity, position, rotation] — as the Lightformers were. */
const PANELS: [number, number, string, number, [number, number, number], [number, number, number]][] = [
  [40, 40, "#ffffff", 0.9, [0, 14, 0], [Math.PI / 2, 0, 0]],
  [30, 12, "#ffffff", 0.8, [24, 6, 0], [0, -Math.PI / 2, 0]],
  [30, 12, "#fff6ea", 0.8, [0, 6, 24], [0, Math.PI, 0]],
];

/** `intensity`: how strongly the panels light the scene (a theme's; changing it does not re-bake). */
export function OfficeEnvironment({ intensity = 1 }: { intensity?: number }) {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);

  useEffect(() => {
    scene.environmentIntensity = intensity;
  }, [scene, intensity]);

  useEffect(() => {
    const panels = new Scene();
    for (const [w, h, color, intensity, position, rotation] of PANELS) {
      const material = new MeshBasicMaterial({ color: new Color(color).multiplyScalar(intensity), side: DoubleSide, toneMapped: false });
      const mesh = new Mesh(new PlaneGeometry(w, h), material);
      mesh.position.set(...position);
      mesh.rotation.set(...rotation);
      panels.add(mesh);
    }
    const pmrem = new PMREMGenerator(gl);
    const target = pmrem.fromScene(panels, 0.04, 0.1, 100);
    scene.environment = target.texture;
    return () => {
      if (scene.environment === target.texture) scene.environment = null;
      target.dispose();
      pmrem.dispose();
      panels.traverse((o) => {
        if (o instanceof Mesh) {
          o.geometry.dispose();
          (o.material as MeshBasicMaterial).dispose();
        }
      });
    };
  }, [gl, scene]);

  return null;
}
