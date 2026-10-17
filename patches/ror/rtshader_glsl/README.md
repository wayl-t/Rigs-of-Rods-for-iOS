# RTShader System GLSL function library (OGRE 1.11.6)

These 13 files are OGRE **v1.11.6**'s own RTSS shader library,
`Samples/Media/RTShaderLib/GLSL/*.glsl`, vendored byte for byte.  They are
installed over RoR's `resources/rtshader/` by `patch_rtshader_media()` in
`../apply_ios_patches.py`; see `PORT_FINDINGS.md` §7.31 for the full story.

## Why they are here

The RTSS function library is data, and it is version locked to the library that
generates the shaders.  The sub-render-states call `FFP_*`/`SGX_*` functions with
hard coded parameter types, and `OgreShaderGLSLESProgramWriter` matches those
calls against the signatures it parses out of these files with
`FunctionInvocation::operator==` — which compares the function name, the return
type, the operand count and every operand's semantic and *type*.

RoR ships the **2009** (OGRE 1.6/1.7) set, while this port links OGRE 1.11.6.
The signatures do not agree:

| function | RoR 2009 | OGRE 1.11.6 |
|---|---|---|
| `SGX_FetchNormal` | `out vec3 vOut` | `out vec3` **and** `out vec4` |
| `SGX_Light_*` | `in vec3 vTSNormal` | `in vec4 vTSNormal` |
| `FFP_Transform` | `mat4/vec4/vec4` only | + `mat4/vec3`, `mat3/vec3`, `mat3x4/vec3` |
| `FFP_Normalize`, `FFP_DerivePointSize`, `FFP_PixelFog_PositionDepth`, `FFP_SampleTexture` | absent | present |

A failed comparison means the function body is never emitted — the device log
showed `Error: Cached function not found SGX_FetchNormal` and
`... SGX_Light_Point_DiffuseSpecular` nine times while entering the main menu —
yet the call and its forward declaration are still written, so the generated
shader references an undefined function and does not link.

OGRE's media is deliberately **not** installed into the SDK prefix
(`build-ios-deps.yml` builds OGRE with `OGRE_BUILD_SAMPLES=FALSE`), so copying
from this directory is the only way to keep generation and library in step.

## Keeping them in step with OGRE

`build-ios-deps.yml` clones exactly `https://github.com/OGRECave/ogre.git`
`--branch v1.11.6`.  If that tag is ever bumped, these files must be refreshed
from the same tag.  `valanchors.py` compares all 13 files (whitespace
normalised) against that tag, and `patches/verify_ipa.py` re-reads
`rorres/rtshader.zip` inside the finished IPA and asserts the 1.11.6 signatures,
so a desync cannot ship silently.

To refresh:

```bash
for f in *.glsl; do
  curl -fsSL "https://raw.githubusercontent.com/OGRECave/ogre/v1.11.6/Samples/Media/RTShaderLib/GLSL/$f" -o "$f"
done
```

RoR's own `resources/rtshader/SampleLib_ReflectionMap.glsl` and
`resources/rtshader/RTShaderSystem.material` are **not** in this set and are
left untouched by the patch.

## Files (sha256 of the LF-normalised content)

```
01a4f3e7fb05776752f9a6d5d08ecc449afa8dd8efec215bd432d005e64de37d  FFPLib_AlphaTest.glsl
9ac7aba8ab88738fe29de877bea4a5ebfaf6c2fe9ba8fffec8138cc80f328536  FFPLib_Common.glsl
d5e3b614d4e0e24eb3cffe0dba98db8a4227e0cdfb482b1266744900f458a56e  FFPLib_Fog.glsl
bb439c53663e297289f94d315dd4a15a7038af14761411ad3a92af5646a75576  FFPLib_Lighting.glsl
6c5d8539a32702ba4712e3148c2ec8eca80365d8642edc499bafb32ae117c758  FFPLib_Texturing.glsl
a2d7002ac719b9c360ed2c2673c8b5316178125f75865330aba5a145b3259644  FFPLib_Transform.glsl
91cb43797eda806ea3d55b9f3d9388c6a9049197742f7f69ee3ae00e89142bd5  SGXLib_DualQuaternion.glsl
27d13209d0b7923370aa601f6b50b093df1c1ae2561880d09efd2939868afce5  SGXLib_IntegratedPSSM.glsl
cfab279327c4dafd53c443bd5d0bb5a5473aeec675de1f03e1fd1d0c942624de  SGXLib_LayeredBlending.glsl
e945a736dfe5a28abf8e9823d2147aa0d8ca90bdf25f4f0db02d7c259c7587db  SGXLib_NormalMapLighting.glsl
192787915d9dadcc14b53abe9b08f51e6faf79111f2962ff1e2bfe71f5e4efea  SGXLib_PerPixelLighting.glsl
005408293be7c5a947e54e66f54b8c46ac4c7687420b929a4270b97ad233374c  SGXLib_TextureAtlas.glsl
26b8a7f963eb205fbf5bdd7876581e664f2bcfd78c00749d2304bafde9273d47  SGXLib_TriplanarTexturing.glsl
```

(90389 bytes in total.)
