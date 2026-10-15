# Журнал зондирования: что реально ломается на Apple

Здесь фиксируются **измеренные факты**, а не предположения. Каждый пункт —
из прогона CI или из чтения исходников, с указанием источника.

---

## Веха 0 — конвейер доставки: ПРОЙДЕНО

Сборка на бесплатном раннере `macos-15` → unsigned IPA → sideload.

| Проверка | Результат |
|---|---|
| Mach-O, архитектура | arm64 (`feedfacf`, cputype `0x0100000c`) |
| Наш C++ внутри бинарника | `rorcore 0.1.0`, `rorcore_beam_force`, `rorcore_self_test` присутствуют |
| Ad-hoc подпись | есть `_CodeSignature/CodeResources` |
| Info.plist | `UIFileSharingEnabled`, `GCSupportsGameMode`, ландшафт, min iOS 16.0 |

Пойманные и исправленные грабли (обе убили бы сборку):
- **CRLF** в `build_ipa.sh` — Windows-наследие, ломает bash на macOS. Лечится
  `.gitattributes` с `* text=auto eol=lf`.
- **Отсутствие флага `+x`** — git на Windows не хранит исполняемый бит,
  `./build_ipa.sh` падал с `Permission denied`. Лечится вызовом `bash build_ipa.sh`.

Отдельно: домен `release-assets.githubusercontent.com` **недоступен** из сети
пользователя (TLS handshake timeout), а artifacts скачиваются нормально. IPA
надо забирать через `gh run download`, а не через Releases.

---

## Веха 1 — разведка системы сборки: ПРОЙДЕНО

### Факты о сборке RoR
- Система сборки: **CMake + Conan** (`cmake/conan_provider.cmake`).
- Зависимости тянутся с ремоута `https://nexus.anotherfoxguy.com/repository/rigs-of-rods/`.
- В `CMakeLists.txt` **всего две ветки платформы**: `if (WIN32)` и `ELSEIF (UNIX)`.
  macOS попадает в `UNIX`, iOS — тоже. Отдельной ветки под Apple нет.
  Ветка `UNIX` жёстко задаёт `-std=c++11` (строка 112).
- Объём: ~180 000 строк C++.

### Обязательные зависимости (13)
`ogre3d 1.11.6.1`, `ois 1.4.1`, `mygui 3.4.0`, `ogre3d-caelum`,
`ogre3d-pagedgeometry`, `angelscript`, `openal-soft`, `socketw`,
`libcurl`, `fmt`, `rapidjson`, `discord-rpc`, `openssl`.

### Прогон 1: macOS
- Conan успешно **скачал 21 готовый бинарник** под macOS.
- Упало на сборке **OIS из исходников**:
  - `src/mac/CocoaJoyStick.mm:78` → `std::unary_function` **удалён в C++17**
  - `OISException.h:76` → макрос `OIS_EXCEPT` использует `throw(...)` —
    **удалено в C++17**
- Вывод: код OIS — из эпохи C++98 и не собирается современным clang в C++17.

### Прогон 1: iOS
- Граф из **33 пакетов разрешился полностью**.
- Упало на `Invalid: 'settings.os.sdk' value not defined` — это ошибка
  профиля Conan, а не свойство RoR. Исправлено добавлением `-s:h os.sdk=iphoneos`.

---

## Веха 2 — OGRE на iOS: КЛЮЧЕВОЙ ВОПРОС

Изначально рендерер считался главной стеной. **Это оказалось неверно.**

### OGRE 1.11.6 имеет полноценную поддержку iOS

Сборка iOS включается флагом `APPLE_IOS` и **принудительно** настраивает:

```cmake
if (APPLE_IOS)
  set(CMAKE_EXE_LINKER_FLAGS "-framework OpenGLES -framework Foundation
       -framework CoreGraphics -framework QuartzCore -framework UIKit")
  set(OGRE_BUILD_RENDERSYSTEM_GLES2 TRUE CACHE BOOL "..." FORCE)
  set(OGRE_STATIC TRUE CACHE BOOL "..." FORCE)
```

```cmake
if (APPLE_IOS)
  set(OGRE_CONFIG_ENABLE_PVRTC TRUE)   # iOS-специфичное сжатие текстур
endif()
```

Инструменты, которые всё это время лежали в репозитории:
- `CMake/toolchain/ios.toolchain.xcode.cmake` — iOS-toolchain
- `SDK/iOS/make_ios.sh` — официальный скрипт сборки OGRE iOS SDK
- `Samples/Browser/include/SampleBrowser_iOS.h` — iOS-браузер примеров
- `OgreMain/src/iOS/OgreFileSystemLayer.mm`, `macUtils.mm` — платформенный слой

### Главное: есть бэкенд EAGL

EAGL — нативный API iOS для контекста OpenGL ES. В OGRE он реализован
**полностью**, а не заглушками:

```
RenderSystems/GLES2/include/EAGL/OgreEAGL2Support.h
RenderSystems/GLES2/include/EAGL/OgreEAGL2View.h
RenderSystems/GLES2/include/EAGL/OgreEAGL2ViewController.h
RenderSystems/GLES2/include/EAGL/OgreEAGL2Window.h
RenderSystems/GLES2/include/EAGL/OgreEAGLES2Context.h
RenderSystems/GLES2/src/EAGL/OgreEAGL2Support.mm
RenderSystems/GLES2/src/EAGL/OgreEAGL2View.mm
RenderSystems/GLES2/src/EAGL/OgreEAGL2ViewController.mm
RenderSystems/GLES2/src/EAGL/OgreEAGL2Window.mm
RenderSystems/GLES2/src/EAGL/OgreEAGLES2Context.mm
```

Это окно, контроллер вида и контекст — то есть весь путь отрисовки от кадра до
экрана. Плюс `OGRE_PLATFORM_APPLE_IOS` учитывается в `OgreRoot.cpp`,
`OgreViewport.cpp`, `OgreFileSystem.cpp`, `OgreDynLib.cpp` и других ключевых
файлах, то есть поддержка iOS прошита по ядру, а не приклеена сбоку.

### Следствие для стратегии

Переписывать рендерер на Metal **не нужно**. Разумный путь — оставить OGRE и
включить его собственный iOS-путь:

- Рендер: OGRE 1.11.6 → GLES2 → EAGL. OpenGL ES на iOS объявлен deprecated
  (с iOS 12), но **функционирует**, включая актуальные версии iOS.
- Это снимает самый дорогой пункт первоначальной оценки.

---

## Веха 3 — сборка OGRE 1.11.6 для iPhone: ПРОЙДЕНО

Два маршрута, оба проверены прогоном CI.

**Маршрут 1** (штатный `CMake/toolchain/ios.toolchain.xcode.cmake`) — не пошёл:
конфликт политик CMake (`cmake_minimum_required 3.3.0` против CMake 4.x).

**Маршрут 2** (современная поддержка: `CMAKE_SYSTEM_NAME=iOS` + `-DAPPLE_IOS=TRUE`,
генератор Xcode) — конфигурация **OK**, сборка:

| Цель | Результат |
|---|---|
| **OgreMain** | BUILD SUCCEEDED (0 ошибок) |
| **RenderSystem_GLES2** | BUILD SUCCEEDED (0 ошибок, много предупреждений о deprecation) |
| **OgreOverlay** | не собрался в этом прогоне из-за ненастроенных зависимостей |

**Промежуточный вывод:** OGRE 1.11.6 **собирается под iOS** современным
инструментарием. Ядро (OgreMain + GLES2/EAGL) есть. Это ключевой результат.

---

## Веха 4 — RoR на macOS: упёрлись в OIS

Попытка собрать RoR на macOS целиком (conan_provider + ninja).

- `conan profile update settings.compiler.cppstd=gnu14` **не сработал**: в Conan 2
  этой команды фактически нет, профиль остался `gnu17`.
- Результат: сборка падает ещё на **зависимости** OIS 1.4.1 —
  `std::unary_function` (удалён в C++17) и `throw(...)` в `OIS_EXCEPT`.
- До собственного кода RoR дело **не доходит**, поэтому списка ошибок RoR у нас
  до сих пор не было.

**Что это значит:** чтобы получить ошибки RoR, надо либо пропатчить OIS, либо
собрать его самому. Ломать conan-рецепт OIS смысла нет — его всё равно придётся
заменять под Apple.

---

## Веха 5 — OGRE iOS: расширенный прогон

Проба маршрута 2 расширена: установка собранного OGRE в staging-каталог
(`ogre-ios-sdk`) и сбор логов в артефакты. Прогон зелёный.

---

## Веха 6 — OIS: сюрприз. iPhone-бэкенд ЕСТЬ

Это **исправление вывода вехи 2**, где было записано, что «у OIS нет iOS-бэкенда».

Исходники OIS живут в `AnotherFoxGuy/conan-OIS` (рецепт экспортирует их сам:
`exports_sources = includes/*, src/*, CMakeLists.txt`). В дереве есть:

```
includes/iphone/iPhoneInputManager.h
includes/iphone/iPhoneMultiTouch.h
includes/iphone/iPhoneAccelerometer.h
includes/iphone/iPhonePrereqs.h
includes/iphone/iPhoneHelpers.h
src/iphone/iPhoneInputManager.mm
src/iphone/iPhoneMultiTouch.mm
src/iphone/iPhoneAccelerometer.mm
```

То есть **мультитач-бэкенд для iPhone в OIS есть**. Но CMake OIS выбирает его
только по переменной `OGRE_BUILD_PLATFORM_APPLE_IOS`:

```cmake
elseif (APPLE)
    if (OGRE_BUILD_PLATFORM_APPLE_IOS)
        set(OIS_PLATFORM iphone)   # src/iphone/*.mm
    else ()
        set(OIS_PLATFORM mac)      # src/mac/*.mm  <- сюда и попадал iPhone
    endif ()
```

А conan-рецепт эту переменную не выставляет. Поэтому при сборке под iPhone OIS
брал **Cocoa-бэкенд** (AppKit/IOKit), которого на iOS нет, — отсюда и ощущение
«у OIS нет iOS».

**Следствие:** OIS, возможно, не нужно выбрасывать. Нужно собрать его самим с
`-DOGRE_BUILD_PLATFORM_APPLE_IOS=TRUE` и подать RoR.

### Как RoR зависит от OIS

Использование сосредоточено и обозримо:

- `conanfile.py` — `self.requires("ois/1.4.1@rigsofrods/custom")`;
- `cmake/DependenciesConfig.cmake:9` — `find_package(OIS REQUIRED)`;
- `source/main/CMakeLists.txt:471` — линковка `ois::ois`;
- заголовки: `pch.h`, `AppContext.h` (наследует `OIS::MouseListener`,
  `OIS::KeyListener`, `OIS::JoyStickListener`), `utils/InputEngine.h`,
  `utils/ForceFeedback.h`, `gui/OverlayWrapper.h`, `gui/GUIUtils.h`,
  `gui/imgui/OgreImGui.h`, `gfx/camera/CameraManager.h`, `gameplay/SceneMouse.h`.

`utils/InputEngine.cpp` работает с клавиатурой/мышью/джойстиком OIS; на iPhone
OIS даёт `MultiTouch`. Значит, даже с собранным OIS **событий клавиатуры/мыши
не будет** — для игры нужен слой, превращающий касания в события RoR.

### Патч-слой

Чтобы не править всё это руками в CI, заведён скрипт
`patches/ror/apply_ios_patches.py`. Он:

1. убирает `ois` из `conanfile.py`;
2. делает `find_package(OIS)` условным (по `ROR_OIS_INCLUDE_DIR`);
3. переносит линковку OIS из `ois::ois` на `ROR_OIS_LIBRARY` и добавляет
   `ROR_OIS_INCLUDE_DIR` в include-пути.

Так RoR можно собрать с **любым** OIS — conan'овским или собранным нами.

### Прогон: OIS под iPhone — ПРОЙДЕНО

Workflow `.github/workflows/probe-ois-ror.yml`, job `ois-ios`:

```
cmake -S ois -B ois/build-ios -G Ninja \
  -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_OSX_DEPLOYMENT_TARGET=16.0 -DCMAKE_OSX_SYSROOT=iphoneos \
  -DCMAKE_CXX_STANDARD=14 -DOGRE_BUILD_PLATFORM_APPLE_IOS=TRUE
```

Результат (прогон `38658043013`):

| | |
|---|---|
| Конфигурация | OK |
| Сборка | `[13/13] Linking CXX static library libOIS.a` |
| Ошибки | **0**; только deprecation-предупреждения |
| Бэкенд | `src/iphone/iPhoneInputManager.mm`, `iPhoneMultiTouch.mm`, `iPhoneAccelerometer.mm` |

Собранное — настоящий `libOIS.a` под arm64 с мультитачем. **OIS выбрасывать не
нужно.**

Цена: в задействованных iPhone-файлах OIS — устаревшие API
(`UIAccelerometer` → CoreMotion, `keyWindow` → сцены). Работать это будет, но
акселерометр и `keyWindow` со временем придётся заменить — уже в нашем коде,
не в OIS.

---

## Веха 7 — собственная сборка RoR на Apple: четыре грабли

Пока собирался весь граф зависимостей RoR, выяснилось четыре вещи.

### 1. На ремоуте `rigs-of-rods-deps` НЕТ бинарников — только рецепты

Каждый пакет при разрешении печатает:

```
<package>: Main binary package '...' missing
<package>: Compatible configurations not found in cache, checking servers
<package>: No compatible configuration found
```

То есть **всё собирается из исходников на раннере**, OGRE в том числе. Это
объясняет, почему первый заход на macOS «скачивал 21 бинарник» и всё равно
падал на OIS: скачивались **ревизии рецептов**, а не собранные библиотеки.

Следствие: первый прогон всегда долгий, и на нём легко упереться в чужую
зависимость раньше, чем в свой код.

### 2. Conan 2: `compiler.cppstd` задаётся через `CMAKE_CXX_STANDARD`

`cmake/conan_provider.cmake` (строки 131-135, 402-403) делает:

```cmake
function(detect_cxx_standard cxx_standard)
    set(${cxx_standard} ${CMAKE_CXX_STANDARD} PARENT_SCOPE)
    if(CMAKE_CXX_EXTENSIONS)
        set(${cxx_standard} "gnu${CMAKE_CXX_STANDARD}" PARENT_SCOPE)
    endif()
endfunction()
...
string(APPEND profile compiler.cppstd=${compiler_cppstd} "\n")
```

Значит `-DCMAKE_CXX_STANDARD=14` у RoR превращается в `compiler.cppstd=gnu14`
для **всего** conan-графа. Это и есть правильный рычаг — а не `conan profile
update`, которого в Conan 2 нет.

### 3. OGRE 1.11 + Ninja = `bad $-escape`

Сборка `ogre3d` из исходников генератором Ninja падает уже на генерации:

```
CMake Error: Running ... ninja -t recompact failed with:
ninja: error: build.ninja:2844: bad $-escape (literal $ must be written as $$)
```

Провайдер копирует `CMAKE_GENERATOR` в conan-профиль
(`tools.cmake.cmaketoolchain:generator=${CMAKE_GENERATOR}`), поэтому лечится
сборкой с `-G"Unix Makefiles"` — тогда и зависимости, и RoR идут через make.

### 4. OGRE 1.11.6 не компилируется под C++17

```
OgreMain/src/OgreLodStrategy.cpp:89: error: no template named 'binary_function' in namespace 'std'
OgreMain/src/OgreLodStrategy.cpp:104: error: no template named 'binary_function' in namespace 'std'
```

`std::binary_function`/`std::unary_function` удалены из libc++ в C++17. То же
самое было у OIS. Под C++14 всё это ещё есть (с deprecation-предупреждением).

Именно поэтому OGRE под iOS в вехе 3 собрался: там генератор Xcode и стандарт не
форсился, а conan-путь выставляет `gnu17`.

### 5. OGRE 1.11 считает Apple Silicon за x86

`OgreMain/include/OgrePlatformInformation.h` определяет CPU **до** того, как
смотрит на ARM:

```c
#if (defined(_MSC_VER) && (defined(_M_IX86) || defined(_M_X64))) || \
    (defined(__GNUC__) && (defined(__i386__) || defined(__x86_64__)))
#   define OGRE_CPU OGRE_CPU_X86
#elif OGRE_PLATFORM == OGRE_PLATFORM_APPLE && defined(__BIG_ENDIAN__)
#   define OGRE_CPU OGRE_CPU_PPC
#elif OGRE_PLATFORM == OGRE_PLATFORM_APPLE
#   define OGRE_CPU OGRE_CPU_X86        /* <- arm64 macOS попадает сюда */
#elif OGRE_PLATFORM == OGRE_PLATFORM_APPLE_IOS && (defined(__i386__) || defined(__x86_64__))
#   define OGRE_CPU OGRE_CPU_X86
#elif defined(__arm__) || defined(_M_ARM) || defined(__arm64__) || defined(__aarch64__)
#   define OGRE_CPU OGRE_CPU_ARM
```

На Apple Silicon ни `__i386__`, ни `__x86_64__` не определены, но ветка
`OGRE_PLATFORM_APPLE` стоит **раньше** ветки ARM. Итог — `OGRE_CPU_X86`, затем
`__OGRE_HAVE_SSE=1`, затем `<xmmintrin.h>`:

```
OgreMain/src/OgreOptimisedUtilSSE.cpp:28
  -> OgreStableHeaders.h -> ... -> <xmmintrin.h>:14:2:
     error: "This header is only meant to be used on x86 and x64 architecture"
```

**iOS не задет.** Условие `__OGRE_HAVE_SSE` (строка 71) явно исключает
`OGRE_PLATFORM_APPLE_IOS`, поэтому на iPhone честно выбирается ARM/NEON —
ровно поэтому OGRE под iOS в вехе 3 и собрался.

Что делать:

* для настольной пробы — раннер `macos-15-intel` (x86_64), и баг просто не
  возникает;
* для настоящей поддержки Apple Silicon этот хедер надо править (проверять
  `__arm64__` в Apple-ветке). Когда будем собирать OGRE сами — под iOS —
  пропатчим заодно: страховка дешёвая.

### 6. «Необязательные» зависимости: что защищено, а что нет

`cmake/DependenciesConfig.cmake` формально делит зависимости надвое:

| Обязательные (`find_package(... REQUIRED)`) | Необязательные (`cmake_dependent_option`, фичи за `USE_*`) |
|---|---|
| OGRE, OIS, MyGUI, fmt, RapidJSON | OpenAL, discord-rpc, SocketW, AngelScript, cURL, Caelum, PagedGeometry |

Чтобы понять, какие «необязательные» на деле обязательны, я прошёл по всем
`#include` этих заголовков, отслеживая **стек препроцессорных условий**.
Первая, наивная попытка («есть ли `#ifdef` в пределах 8 строк выше») дала
неверный ответ — и это важно: `SkyManager.cpp:34 #include <Caelum.h>` выглядит
незащищённым, а на самом деле строка 22 того же файла — это `#ifdef USE_CAELUM`.

Итог по каждой библиотеке:

| Библиотека | Включений | Вне guard | Вывод |
|---|---|---|---|
| **OpenAL** | 3 | `source/main/pch.h:41-42` — `<AL/al.h>`, `<AL/alc.h>` | **обязательна** |
| **AngelScript** | 64 | 59 файлов, напр. `utils/memory/RefCountingObjectPtr.h:11`, `scripting/GameScript.h:30` | **обязательна** |
| SocketW | 6 | `source/main/network/Network.cpp:40` | **обязательна** |
| PagedGeometry | 4 | `gameplay/RandomTreeLoader.h:25-27` — `PagedGeometry.h`, `PropertyMaps.h`, `TreeLoader2D.h` | нужна, если этот заголовок компилируется |
| Caelum | 6 | — | защищена (`SkyManager.cpp:22`) |
| discord-rpc | 3 | — | защищена |
| cURL | 2 | — | защищена |

Второй `"SocketW.h"` (`scripting/server/ServerScriptEngine.cpp:38`) лежит внутри
`#ifdef USE_ANGELSCRIPT` (строка 28), то есть SocketW нужен ровно тогда, когда
включён AngelScript, — а он включён всегда.

`RefCountingObjectPtr.h` особенно неприятен: он попадает в PCH через
`Application.h -> ForwardDeclarations.h`, поэтому без AngelScript не собирается
вообще ничего:

```
fatal error: 'angelscript.h' file not found
    source/main/utils/memory/RefCountingObjectPtr.h:11:10
In file included from .../pch.h:38 -> gui/imgui/imgui.h -> imconfig.h ->
    Application.h -> ForwardDeclarations.h -> RefCountingObjectPtr.h
```

Вывод: без правки десятков файлов список зависимостей не урезать. Практически
обязательны OGRE, OIS, MyGUI, fmt, RapidJSON, **AngelScript, OpenAL, SocketW**
(и PagedGeometry). Caelum, cURL и discord-rpc выкинуть *можно* — их `#include`
честно за `#ifdef`, — но это временно убирает небо, докачку модов и Discord;
для настоящего порта их надо вернуть.

### 6.1. Почему macOS оказался плохой площадкой для пробы

RoR официально поддерживает Windows и Linux; macOS — нет, и это видно по
рецептам зависимостей из `rigs-of-rods-deps`. Три независимых падения подряд,
все — платформенные баги зависимостей, а не ошибки RoR:

| Зависимость | Ошибка на macOS |
|---|---|
| `ogre3d` | `OgrePlatformInformation.h` считает Apple Silicon за x86 → `<xmmintrin.h>` |
| `ogre3d` (плагин Cg) | `cg-toolkit` кладёт в macOS-пакет **Linux ELF** `libCg.so`/`libCgGL.so` → `ld: unknown file type` |
| `angelscript/2.38.0` | `as_callfunc_x64_gcc.cpp` — x86-asm с `.cfi_def_cfa_register rsp`, ассемблер macOS его отвергает: `error: invalid CFI advance_loc expression` |

Последнее и стало остановкой: `angelscript` обязателен (см. раздел 6), но его
рецепт на macOS не собирается. Поэтому вместо дальнейшей борьбы с настольными
рецептами переходим к сборке зависимостей **сразу под iOS** — то, что всё равно
нужно делать.

---

### 7. Что именно даёт OGRE под iOS (и чего не хватает)

Проба `probe-ogre-ios.yml` описана двумя маршрутами:

* **маршрут 1** — собственный iOS-toolchain самого OGRE
  (`CMake/toolchain/ios.toolchain.xcode.cmake`). Падает уже на конфигурации:
  в артефактах есть только `r1-configure.log`, ни одного `r1-build-*.log`.
* **маршрут 2** — современный CMake: `-DCMAKE_SYSTEM_NAME=iOS
  -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET=16.0`.
  Работает: `OgreMain` и `RenderSystem_GLES2` заканчиваются `** BUILD SUCCEEDED **`.

Сводка OGRE под iOS (маршрут 2):

```
Building components:
  + HLMS [BETA]
  + MeshLodGenerator
  + Paging
  + Property
  + RTShader System (+ Core Shaders, Extensions Shaders)
  + Terrain
Building plugins:
  + STBI codec (generic)
  + Particle FX
Building rendersystems:
  + OpenGL ES2/ ES3
Build type: static
```

То есть четыре из шести компонентов, которые RoR требует
(`find_package(OGRE 1.11 REQUIRED COMPONENTS Bites Overlay Paging RTShaderSystem MeshLodGenerator Terrain)`,
`cmake/DependenciesConfig.cmake:6`), уже собираются: MeshLodGenerator, Paging,
RTShaderSystem, Terrain. **Нет `Overlay` и `Bites`.**

Причина — FreeType. В логе конфигурации дважды:

```
-- Could NOT find Freetype (missing: FREETYPE_LIBRARY FREETYPE_INCLUDE_DIRS)
```

и в списке ненайденных также `zziplib`, `freeimage`, `OpenGL`, `OpenEXR`;
из системного нашлись только `zlib` и `OpenGL ES 2.x`.

А без них RoR не соберётся — `source/main/pch.h`:

```
34: #include <Bites/OgreWindowEventUtilities.h>
35: #include <Overlay/OgreFontManager.h>
```

`-DOGRE_BUILD_DEPENDENCIES=TRUE` (OGRE собирает зависимости сам) не спасает:

* `zziplib` — `PhaseScriptExecution ... zzipfseeko.pc` и `compat/zzip-io.h`,
  `** BUILD FAILED **`;
* `freetype` — `CMake Error at CMakeLists.txt:78 (cmake_minimum_required):
  Compatibility with CMake < 3.5 has been removed from CMake`; его
  `ExternalProject` не получает наш `-DCMAKE_POLICY_VERSION_MINIMUM=3.5`.

Вывод: для iOS нужна отдельная кросс-сборка зависимостей. Первый по важности —
**FreeType** (он открывает `Overlay`, `Bites` и шрифты MyGUI), дальше OGRE,
MyGUI, OIS. `freeimage` можно не тащить: в OGRE уже собирается `STBI codec`
(jpg/png/bmp/tga — этого хватает контенту RoR); `zziplib` нужен только если
RoR действительно грузит `.zip`.

---

## Что всё ещё неизвестно / не проверено

- **Собственный код RoR на Apple clang** — job `ror-macos` того же прогона
  собирает OIS самим и смотрит, как далеко заходит RoR. Это список ошибок,
  который мы ждали с вехи 4.
- **MyGUI (GUI) на iOS.** У RoR 78 файлов GUI на MyGUI. MyGUI — чистый C++ и
  рисуется через OGRE, но ему нужен поток событий ввода, а его на iPhone пока нет.
- **Прочие зависимости под iOS:** `mygui`, `ogre3d-caelum`, `ogre3d-pagedgeometry`,
  `socketw`, `openal-soft`, `angelscript`, `discord-rpc`. Готовых бинарников под
  iOS не существует — придётся собирать из исходников либо отключать.
- **Контент.** Машины и ландшафты RoR должны попасть в контейнер приложения
  (как в GTA V iOS кладут данные в Documents через Файлы).

---

## Уточнённая карта порта

| Компонент | Судьба |
|---|---|
| Отрисовка (OGRE 1.11.6) | оставить, включить штатный iOS-путь GLES2/EAGL |
| OIS (ввод) | собрать самим с iPhone-бэкендом; события касаний → события RoR |
| MyGUI (интерфейс) | оставить, подать в него события касаний |
| Caelum, PagedGeometry | плагины OGRE, едут вместе с ним |
| Физика | чистый C++, должна собраться как есть |
| Парсеры форматов | чистый C++, должны собраться как есть |
| `main.cpp`, `PlatformUtils` | заменить платформенной обвязкой iOS |
| Контент | упаковать в контейнер приложения |

Это **порт**, а не переписывание — то есть именно то, что было нужно.
