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

### 7.1. Что должно войти в iOS-набор зависимостей

Список следует не из `conanfile.py`, а из того, какие заголовки включаются
безусловно (раздел 6) и как `cmake/DependenciesConfig.cmake` включает фичи:

```cmake
28: find_package(OpenAL)
29: cmake_dependent_option(ROR_USE_OPENAL     ON "OPENAL_FOUND"                       OFF)
36: find_package(SocketW)
37: cmake_dependent_option(ROR_USE_SOCKETW    ON "TARGET SocketW::SocketW"            OFF)
44: find_package(Angelscript)
45: cmake_dependent_option(ROR_USE_ANGELSCRIPT ON "TARGET Angelscript::angelscript"   OFF)
52: find_package(Caelum)
53: cmake_dependent_option(ROR_USE_CAELUM      ON "TARGET Caelum::Caelum"             OFF)
56: find_package(PagedGeometry)
57: cmake_dependent_option(ROR_USE_PAGED       ON "TARGET PagedGeometry::PagedGeometry" OFF)
```

| Библиотека | Нужна под iOS | Почему |
|---|---|---|
| OGRE 1.11 (Bites, Overlay, Paging, RTShaderSystem, MeshLodGenerator, Terrain) | **да** | `DependenciesConfig.cmake:6`, `pch.h:34-35` |
| FreeType | **да** | открывает `Overlay`/`Bites` и шрифты MyGUI |
| MyGUI 3.4 | **да** | `find_package(MyGUI REQUIRED)`, 78 файлов GUI |
| OIS | **да** | уже собран под iOS (веха 5) |
| fmt, RapidJSON | **да** | REQUIRED / header-only |
| **AngelScript 2.38** | **да** | 59 безусловных включений. **arm64 поддержан**: в `sdk/angelscript/source` есть `as_callfunc_arm64.cpp` и отдельный `as_callfunc_arm64_xcode.S` |
| **OpenAL (openal-soft)** | **да** | `pch.h:41-42` |
| **SocketW** | **да** | `Network.cpp:40` |
| OpenSSL | **да** | нужен SocketW (`DependenciesConfig.cmake:38-41`) |
| PagedGeometry | вероятно | `RandomTreeLoader.h:25-27` |
| Caelum, cURL, discord-rpc | нет (пока) | их `#include` за `#ifdef`; без `find_package` фича просто выключается |
| zziplib, zlib, libpng, libwebp, jasper | нет (пока) | кодек `STBI` в OGRE закрывает jpg/png; zip нужен только если RoR грузит `.zip` |

Деталь OGRE, которую легко упустить: решения про iOS завязаны на переменную
**`APPLE_IOS`**, а не на `CMAKE_SYSTEM_NAME`:

```
CMakeLists.txt:188  if (MSVC OR ANDROID OR EMSCRIPTEN OR APPLE_IOS)
CMakeLists.txt:332  cmake_dependent_option(OGRE_BUILD_PLUGIN_CG ... "Cg_FOUND;NOT APPLE_IOS;...")
```

Её выставляет **только** `CMake/toolchain/ios.toolchain.xcode.cmake:37`
(`set (APPLE_IOS True)`). Современный `-DCMAKE_SYSTEM_NAME=iOS` её не ставит —
поэтому в маршруте 2 она передаётся вручную (`-DAPPLE_IOS=TRUE`). Если собирать
OGRE *рецептом conan* с iOS-профилем, её придётся добавить через `user_toolchain`,
иначе OGRE сконфигурируется как macOS (и потянет Cocoa-код окна).

### 7.2. Первый iOS-SDK: FreeType + OGRE собраны

Прогон `38669963734` (`build-ios-deps.yml`) собрал FreeType и OGRE под
`iphoneos/arm64` — **все 13 библиотек**:

```
libOgreMainStatic.a                   libOgreBitesStatic.a
libOgreGLSupportStatic.a              libOgrePagingStatic.a
libOgreOverlayStatic.a                libOgreTerrainStatic.a
libOgreRTShaderSystemStatic.a         libOgreMeshLodGeneratorStatic.a
libOgreHLMSStatic.a                   libRenderSystem_GLES2Static.a
libPlugin_OctreeSceneManagerStatic.a  libPlugin_ParticleFXStatic.a
libCodec_STBIStatic.a
```

То есть **`Overlay` и `Bites` теперь есть** — достаточно было дать OGRE FreeType
(`-DFREETYPE_LIBRARY=.../libfreetype.a`, `-DFREETYPE_INCLUDE_DIRS=.../include/freetype2`
на наш iOS-префикс). Вся сборка — 3м26с.

Но шаг `install` упал:

```
CMake Error at OgreMain/cmake_install.cmake:62 (file):
  file INSTALL cannot find
  ".../ogre/build/lib/$(PLATFORM_NAME)/Release/libOgreMainStatic.a"
```

Причина — `CMake/Utils/OgreConfigTargets.cmake`:

```cmake
16:   if(NOT OGRE_BUILD_PLATFORM_ANDROID AND NOT APPLE_IOS)
17:     set(PLATFORM_NAME "macosx")
18:   elseif(APPLE_IOS)
19:     set(PLATFORM_NAME "$(PLATFORM_NAME)")     # <- литерал, не переменная
26:     set(OGRE_ARCHIVE_OUTPUT ${PROJECT_BINARY_DIR}/lib/${PLATFORM_NAME})
```

Для iOS OGRE кладёт библиотеки в каталог с **буквальным** именем `$(PLATFORM_NAME)`
и рассчитывает, что Xcode раскроет его при сборке. Xcode раскрывает (в `iphoneos`),
а CMake-скрипт `install()` — нет, поэтому ищет файл по несуществующему пути.

Обход, не трогая исходники OGRE: строка 24 — `if (NOT OGRE_ARCHIVE_OUTPUT)`,
значит явное значение отключает ветку с литералом:

```
-DOGRE_ARCHIVE_OUTPUT=$PWD/ogre/build/lib/iphoneos
-DOGRE_LIBRARY_OUTPUT=$PWD/ogre/build/lib/iphoneos
-DOGRE_RUNTIME_OUTPUT=$PWD/ogre/build/bin
```

Итоговый layout установки (по установленному `OgreTargets-release.cmake`):

```
${prefix}/lib/Release/libOgreMainStatic.a
${prefix}/lib/Release/libOgreBitesStatic.a
${prefix}/lib/Release/libOgreOverlayStatic.a
${prefix}/lib/Release/libOgreRTShaderSystemStatic.a
${prefix}/lib/Release/OGRE/libRenderSystem_GLES2Static.a
${prefix}/lib/Release/OGRE/libPlugin_OctreeSceneManagerStatic.a
${prefix}/lib/Release/OGRE/libCodec_STBIStatic.a
```

Плюс `CMake/OGREConfig.cmake`, `CMake/OgreTargets*.cmake` и pkgconfig-файлы
(`OGRE.pc`, `OGRE-Overlay.pc`, `OGRE-Bites.pc`, …) — то есть RoR сможет
подключиться обычным `find_package(OGRE 1.11 REQUIRED COMPONENTS ...)`.

### 7.3. Три ловушки следующих зависимостей

**AngelScript: `ARCHFLAGS` не понимает `arm64`.** В
`sdk/angelscript/projects/cmake/CMakeLists.txt`:

```cmake
24: set(ARCHFLAGS "${CMAKE_SYSTEM_PROCESSOR}")   # под iOS это "arm64"
25: if (APPLE AND NOT IOS)                       # под iOS ветка не срабатывает
26:   if (${CMAKE_SYSTEM_PROCESSOR} MATCHES "arm")
27:     set(ARCHFLAGS "aarch64")
117: if(${ARCHFLAGS} MATCHES "^arm")             # "arm64" совпадает -> arm32-файлы
127: if(${ARCHFLAGS} MATCHES "^aarch64")          # не совпадает -> arm64-файлов нет
```

Современный `-DCMAKE_SYSTEM_NAME=iOS` не определяет переменную `IOS`, поэтому
CMake берёт **32-битный** `as_callfunc_arm.cpp` вместо
`as_callfunc_arm64_xcode.S`. Лечится однострочной правкой условия на `if (APPLE)`.

Зато AngelScript сам ставит `lib/cmake/Angelscript/AngelscriptConfig.cmake` с
`NAMESPACE Angelscript::` — то есть даёт ровно тот `Angelscript::angelscript`,
который проверяет RoR (`cmake/DependenciesConfig.cmake:45`). Собирать его самим
даже удобнее, чем через conan.

**MyGUI: четыре отдельные ловушки.** Первая попытка собрать MyGUI 3.4.0 для
iOS падала на конфигурации — и сразу по трём причинам:

1. **Carbon/Cocoa объявлены обязательными.** `CMake/Dependencies.cmake:100`
   открывает `if (APPLE)` и делает `find_package(Carbon)` и
   `find_package(Cocoa)` **требуемыми** (`macro_log_feature(..., TRUE, ...)`).
   На iOS `APPLE` истинно, а фреймворков нет — конфигурация умирает с
   «The following REQUIRED packages could NOT be located: freetype, Carbon,
   Cocoa». MyGUI нужен Carbon только для macOS-слоя ввода, которого на iOS нет.

2. **Carbon линкуется и в самих библиотеках.** `MyGUIEngine/CMakeLists.txt:51,66`
   и `Platforms/Ogre/OgrePlatform/CMakeLists.txt:25` добавляют
   `include_directories(/usr/local/include /usr/include)` и
   `target_link_libraries(... ${Carbon_LIBRARY})`. На iOS `find_library(Carbon)`
   даёт `CARBON_LIBRARY-NOTFOUND`, а `NOTFOUND` в `target_link_libraries` — это
   фатальная ошибка уже на генерации.

3. **Свой `FindFreetype.cmake` ждёт старые имена переменных.**
   `CMake/Packages/FindFreetype.cmake` ищет `FREETYPE_INCLUDE_DIR`,
   `FREETYPE_FT2BUILD_INCLUDE_DIR` и `FREETYPE_LIBRARY_REL` — а не
   `FREETYPE_INCLUDE_DIRS` / `FREETYPE_LIBRARY`, которые ему передавались.
   Поэтому он через `use_pkgconfig` цеплял **домашний freetype из /opt/homebrew**
   (macOS-сборку!) и всё равно писал `Could not locate FREETYPE`.

Все три лечатся одним патчем: каждое `if (APPLE)` в этих трёх файлах
становится `if (APPLE AND NOT CMAKE_SYSTEM_NAME STREQUAL "iOS")`, а в
конфигурацию передаются точные имена переменных `FREETYPE_INCLUDE_DIR`,
`FREETYPE_FT2BUILD_INCLUDE_DIR`, `FREETYPE_LIBRARY_REL` плюс
`-DCMAKE_PREFIX_PATH=$IOS_PREFIX`. Заодно фиксируется `ZLIB_LIBRARY`/`ZLIB_INCLUDE_DIR`
на `libz.tbd` из iOS SDK, чтобы `find_package(ZLIB)` не оставил `NOTFOUND`.

4. **`MYGUI_STATIC=TRUE` переименовывает библиотеки.** В
   `CMake/Utils/MyGUIConfigTargets.cmake:475`
   (`set_target_properties(... OUTPUT_NAME ${PROJECTNAME}Static)`) получаются
   `libMyGUIEngineStatic.a` и `libMyGUI.OgrePlatformStatic.a`. А собственный
   `cmake/find-modules/FindMyGUI.cmake` самого RoR ищет **без** суффикса —
   `find_library(... MyGUIEngine ...)`. Патч убирает `Static` из `OUTPUT_NAME`.
   Остальное при `MYGUI_STATIC=TRUE` правильно: `MYGUI_LIB_TYPE` становится
   `STATIC`, а на `UNIX` (куда iOS попадает) библиотеки ставятся в `lib/`, а не
   в `lib/Release`.

**Про `MyGUI::MyGUI` — прежний вывод был неверен.** У RoR есть собственный
`cmake/find-modules/FindMyGUI.cmake`, который сам создаёт IMPORTED-цели
`MyGUI::MyGUI` и `MyGUI::OgrePlatform`; `find_package` по умолчанию идёт
**сначала в Module mode**, поэтому используется именно он, а не config-файл.
Вдобавок `CMake/Templates/MYGUIConfig.cmake.in` в 3.4.0 вообще никуда не
устанавливается (правила `install()` для него в дереве нет). Свой
`lib/cmake/MyGUI/MyGUIConfig.cmake` мы всё равно дописываем — как страховку на
случай config mode.

**`MYGUI_EXPORT` на Apple пуст.** `MyGUIEngine/include/MyGUI_Platform.h:85`
делает его `__attribute__((visibility("default")))` только при
`MYGUI_GCC_VISIBILITY`, а `MYGUI_STATIC` влияет лишь на Windows
(`__declspec`). Для статической линковки это неважно.

**Остальное — прямолинейно.** `openal-soft` собирается обычным
`-DCMAKE_SYSTEM_NAME=iOS`. Проверено, что `APPLE_IOS` в OGRE выбирает
GLES2 и отключает GL/Cg/tools — то есть «не найден OpenGL» в логе это норма.

### 7.4. Остальные зависимости: OIS, fmt, RapidJSON, SocketW, PagedGeometry

Все они собираются для iphoneos/arm64 и укладываются в тот же префикс
`ios-sdk`.

- **OIS** — `https://github.com/AnotherFoxGuy/conan-OIS.git`, рецепт ровно тот
  же, что в `probe-ois-ror.yml`: `-DOGRE_BUILD_PLATFORM_APPLE_IOS=TRUE` включает
  настоящий iPhone-бэкенд (`src/iphone/*.mm`, multitouch). Даёт `libOIS.a`;
  заголовки (`OIS.h`, `OISMouse.h`, …) кладутся плоско в `include/OIS/`, потому
  что RoR включает их как `<OIS.h>`, а `ROR_OIS_INCLUDE_DIR` указывает прямо на
  этот каталог.

- **fmt 12.2.0** — требует всего лишь `cxx_std_11` и ставит
  `lib/cmake/fmt/fmt-config.cmake` с целью `fmt::fmt`. Собирается с
  `-DCMAKE_CXX_STANDARD=14`, как и все остальные зависимости.

- **RapidJSON** — header-only; в `lib/cmake/RapidJSON/RapidJSONConfig.cmake`
  дописывается только `RapidJSON_INCLUDE_DIRS` (RoR читает именно её,
  `source/main/CMakeLists.txt:474`).

- **SocketW** — `https://github.com/AnotherFoxGuy/SocketW.git`. У него
  `cmake_minimum_required(VERSION 3.0.2)`, поэтому нужен
  `-DCMAKE_POLICY_VERSION_MINIMUM=3.5`. Ставит `SocketWTargets.cmake` с
  `NAMESPACE SocketW::` → ровно тот `SocketW::SocketW`, который проверяет RoR
  (`cmake/DependenciesConfig.cmake`). `find_package(OpenSSL)` там **не
  REQUIRED**, а master-server у RoR — обычный TCP, поэтому OpenSSL не
  собирается вообще; чтобы домашний OpenSSL не подмешался, передаётся
  `-DCMAKE_DISABLE_FIND_PACKAGE_OpenSSL=TRUE`.

- **PagedGeometry 1.2.0** — `https://github.com/AnotherFoxGuy/ogre-pagedgeometry.git`
  (тег `v1.2.0`). Нужен потому, что `gameplay/RandomTreeLoader.h` включает
  `PagedGeometry.h` без всяких `#ifdef`. Сборочная система 2009 года умеет
  искать OGRE только через pkg-config, а `OGRE.pc` из нашего префикса
  непригоден: в нём зашиты X11/freeimage/boost и `libdir=${prefix}/lib`, тогда
  как настоящие библиотеки лежат в `lib/Release` (и называются, например,
  `libOgreMainStatic.a`, а не `libOgreMain.a`). Библиотека — это 12
  трансляционных единиц, которым нужны только заголовки OGRE, поэтому
  `CMakeDependenciesConfig.txt` заменяется на пустышку, а пути передаются через
  `-DOgre_INCLUDE_DIRS` / `-DOgre_LIBRARY_DIRS` / `-DOgre_LIBRARIES`. Ставится
  как `include/PagedGeometry/*.h` + `lib/libPagedGeometry.a` — именно туда
  смотрит `cmake/find-modules/FindPagedGeometry.cmake` (он сам создаёт target
  `PagedGeometry::PagedGeometry`, так что config-файл не нужен).

- **Версия C++.** Все зависимости собираются с `-DCMAKE_CXX_STANDARD=14`. Это
  принципиально: у AppleClang 17 по умолчанию `gnu++17`, а OGRE 1.11.6 на C++17
  не компилируется (`std::binary_function`), и MyGUI/PagedGeometry тянут
  заголовки OGRE. Сам RoR собирается с `-std=c++11` (`CMakeLists.txt:112`,
  ветка `ELSEIF (UNIX)`, куда iOS попадает как `UNIX`+`APPLE`).

---

### 7.5. Первый полный прогон iOS-набора: девять из десяти

Прогон `38675510250` собрал **все** зависимости, включая MyGUI, OIS, fmt,
RapidJSON, SocketW, AngelScript и openal-soft; споткнулся он ровно на одном
шаге — `Verify full iOS SDK`, потому что PagedGeometry не поставила ни
библиотеки, ни заголовков. Вся сборка заняла ~6 минут.

Причина — не сборочная система, а собственно код:

```
source/GrassLoader.cpp:111:74: error: member access into incomplete type 'Technique'
source/GrassLoader.cpp:1446:28: error: member access into incomplete type 'Ogre::SceneNode'
source/GrassLoader.cpp:1447:6:  error: member access into incomplete type 'SceneNode'
```

`GrassLoader.cpp` дёргает `layer->material->getTechnique(0)->getPass(0)` и
методы `Ogre::SceneNode`, не включая при этом ни `OgreTechnique.h`, ни
`OgreSceneNode.h`. Код 2009 года писан в расчёте на «один большой `Ogre.h`»,
который раньше подключался транзитивно. В нашем OGRE 1.11.6 — не подключается,
и один сломанный файл останавливает `make`, из-за чего не собирается вообще
ничего (библиотека не появляется, `install` не ставит ни заголовков, ни `.a`).

Лечение простое и безобидное: сборка дописывает `#include <Ogre.h>` первой
строкой в каждый `pagedgeometry/source/*.cpp`, если его там ещё нет. `Ogre.h`
тянет только заголовки OgreMain (`OgreSceneNode.h`, `OgreTechnique.h`,
`OgreSceneManager.h`, `OgrePass.h`, …) — никаких компонентов — так что это
ровно то, что нужно, и сразу закрывает всё семейство подобных ошибок. Сборка
PagedGeometry вдобавок переведена на `make -k`, чтобы одна ошибка больше не
прятала остальные.

Прогон `38677214200` это подтвердил: все одиннадцать единиц трансляции
скомпилировались без единой ошибки, и `libPagedGeometry.a` слинковалась. Но
`install` всё равно не выполнился, потому что проверка искала архив в
`pagedgeometry/build/`, а он оказался в `pagedgeometry/lib/`. Причина —
устаревшая переменная `LIBRARY_OUTPUT_PATH` в корневом `CMakeLists.txt`
PagedGeometry (строки 36-39): она задана **без** префикса `CMAKE_`, и CMake её
всё равно уважает для библиотечных целей, в отличие от соседних
`LIBRARY_OUTPUT_DIRECTORY`/`ARCHIVE_OUTPUT_DIRECTORY`. Исправлено (коммит
`44412ba`): архив теперь ищется по всему дереву `pagedgeometry`, цель `install`
запускается всегда, когда архив найден, а сверху добавлено ручное копирование
как страховка — и заголовков из `include/`, и **сгенерированного**
`PagedGeometryConfig.h` (его `PagedGeometry.h` подключает на строке 11, а
генерируется он в `pagedgeometry/build/include/`).

### 7.6. Чтобы конфиг OGRE заработал у потребителя

`<prefix>/CMake/OGREConfig.cmake` собран CMake'ом из шаблона OGRE и для нашего
статического iOS-префикса содержит два подвоха, которые вылезут уже при
конфигурировании RoR:

1. `OgreTargets.cmake` записывает **абсолютный** путь к `libfreetype.a` той
   машины, где собирался OGRE (`OgreOverlay` линкует FreeType). Совпадает это
   с рабочим каталогом RoR-прогона только по счастливой случайности (GitHub
   всегда монтирует репозиторий в `/Users/runner/work/ror-ios/ror-ios`), и
   потому `build-ror-ios.yml` после скачивания артефакта переписывает этот
   префикс на фактический.
2. Там же есть `$<LINK_ONLY:ZLIB::ZLIB>`, но `OGREConfig.cmake` **не** создаёт
   эту цель. CMake валит генерацию сообщением «the target was not found».
   Поэтому в `OGREConfig.cmake` дописывается `include(CMakeFindDependencyMacro)`
   и `find_dependency(ZLIB)`, а `ZLIB_LIBRARY`/`ZLIB_INCLUDE_DIR` жёстко
   указывают в sysroot iOS, чтобы не подмешался домашний zlib.

Заодно: `OGRE_LIBRARIES` мешает имена imported-целей с одним «плоским» именем
`OgreGLSupportStatic`, а `OGRE_LIBRARY_DIRS` (`<prefix>/lib`, `<prefix>/lib/OGRE`)
не содержит настоящего каталога `lib/Release`, где лежат архивы. Поэтому архивы
из `lib/Release` и `lib/Release/OGRE` копируются «плоско» в `<prefix>/lib`, а на
случай чего в линковку добавляется `-L`.

То же соображение про подмену с хоста касается OpenAL: CMake'овский модуль
`FindOpenAL` на Apple сначала пробует `OpenAL.framework`, а на macOS он есть
(устаревший). Поэтому `OPENAL_LIBRARY`/`OPENAL_INCLUDE_DIR` тоже передаются
явно, а поиск фреймворков переводится в `-DCMAKE_FIND_FRAMEWORK=LAST`.

### 7.7. Следующий шаг: `build-ror-ios.yml`

Добавлен отдельный workflow, который **не** пересобирает зависимости, а
скачивает готовый артефакт `ios-sdk` из последнего завершённого прогона
`build-ios-deps.yml` (артефакт грузится с `if: always()`, поэтому переживает
провалившуюся проверку). Дальше он нормализует префикс (см. 7.6), клонирует
свежий RoR, применяет `patches/ror/apply_ios_patches.py`, конфигурирует проект
как `-DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_ARCHITECTURES=arm64` с генератором
Unix Makefiles и собирает `make -k`.

Смысл прогона — **не** получить приложение, а получить полный список мест, где
собственный C++ RoR не собирается под iOS. Для этого включён `-k` (продолжать
после ошибок), а все ошибки `error:` вытаскиваются в итоговую сводку прогона.
Ошибки линковки ожидаемы и не считаются показательными: фреймворки (UIKit,
OpenGLES, AudioToolbox, …) будут добавляться уже Xcode-проектом приложения.

### 7.8. Платформенный слой RoR: что уже точно сломано на iOS

Это результаты разбора исходников RoR (свежий `HEAD`), а не догадки. Здесь
собраны места, которые гарантированно не соберутся или гарантированно упадут
на iOS — их надо править в любом случае.

**1. `OGRE_PLATFORM` на iOS — это НЕ `OGRE_PLATFORM_APPLE`.**

`OgrePlatform.h:149-155`:

```c
#elif defined(__APPLE_CC__)
#   include "Availability.h"
#   ifdef __IPHONE_OS_VERSION_MIN_REQUIRED
#       define OGRE_PLATFORM OGRE_PLATFORM_APPLE_IOS   // = 4
#   else
#       define OGRE_PLATFORM OGRE_PLATFORM_APPLE       // = 3
#   endif
```

Это **разные** значения. Любая ветка `#if OGRE_PLATFORM == OGRE_PLATFORM_APPLE`
на iOS просто не выполняется. Всего в дереве RoR 36 таких упоминаний; важные:

- `AppContext.cpp:521-533` — определение домашнего каталога. На iOS не
  срабатывает ни `WIN32`, ни `LINUX`, ни `APPLE` → `ror_homedir` остаётся
  пустой строкой → `sys_user_dir` пустой → все пользовательские пути битые.
- `ErrorUtils.cpp:36-40` — там `#define _L` определяется только для `WIN32`,
  `APPLE` и `LINUX`. На iOS **ни одна ветка не срабатывает, и `_L` остаётся
  неопределённым** → `_L("…")` на строках 49-50 превращается в вызов
  необъявленной функции → **ошибка компиляции**.
- `InputEngine.cpp:399-401` — `#define strnlen(str,len) strlen(str)` только для
  `APPLE`. На iOS либо повезёт (Darwin объявляет `strnlen`), либо получим
  «call to undeclared function».

**2. `PlatformUtils.cpp` — путь к исполняемому файлу.**

`#ifdef _MSC_VER` / `#else`; iOS идёт по POSIX-ветке, где
`GetExecutablePath()` = `readlink("/proc/self/exe")`. **На iOS нет `/proc`** →
функция вернёт пустую строку → `AppContext::SetUpProgramPaths()` покажет
«Error while retrieving program directory path» и вернёт `false` → **старт
движка прервётся**. Нужен вариант для iOS (`[[NSBundle mainBundle] bundlePath]`).

Приятная новость: `GetUserHomeDirectory()` = `getenv("HOME")` — на iOS это
песочница приложения, то есть **работает как есть**.

**3. OIS на iPhone даёт только MultiTouch и акселерометр — ни мыши, ни
клавиатуры.**

В бэкенде OIS (`src/iphone/iPhoneInputManager.mm`) список устройств — это
`OISMultiTouch` и `OISJoyStick` (акселерометр). `createInputObject` для
`OISMouse`/`OISKeyboard` бросает исключение. В `InputEngine::setup()` обе
попытки создания обёрнуты в `try/catch`, поэтому **ошибка не фатальна, но
`mMouse` и `mKeyboard` остаются нулевыми** (они инициализированы нулём в
конструкторе, строки 423-424).

Дальше — гарантированное падение, причём **сразу**:

- `InputEngine::InputEngine()` (строка 434) сразу вызывает
  `windowResized(...)`, а `windowResized()` (строка 657) делает
  `mMouse->getMouseState()` при `mMouse == 0` → **падение в конструкторе**.
- `InputEngine::Capture()` (строки 637-638) безусловно вызывает
  `mKeyboard->capture()` и `mMouse->capture()` → **падение**.
- `getMouseState()` (613) и `getKeyNameForKeyCode()` и т.п. — та же история.

Поэтому «просто положить свой слой поверх OIS» недостаточно: нужно, чтобы у
`InputEngine` на iOS всегда были ненулевые `mMouse`/`mKeyboard` (свои
заглушки), либо чтобы все обращения были под проверкой. Второй вариант
предпочтительнее: интерфейс `InputEngine` остаётся, а события касаний
синтезируют те же `EV_*`, что и клавиатура.

Хорошая новость: **в iOS-окне OGRE есть атрибут `"WINDOW"`**
(`OgreEAGL2Window.mm:455-459` возвращает `mWindow`), причём есть и `"VIEW"`, и
`"VIEWCONTROLLER"`. Значит, `InputEngine::setup()` с его
`getCustomAttribute("WINDOW", &hWnd)` и `pl.insert("WINDOW", TOSTRING(hWnd))`
передаст в OIS настоящий `UIWindow` — эта часть работает без правок.

**4. `messagePump()` на iOS — пустышка.**

`OgreWindowEventUtilities.cpp:174-212` реализует `messagePump()` только для
`WIN32` и `LINUX`; на остальных платформах это пустая функция. Значит, главный
цикл RoR не может «крутиться» в своём `while`, полагаясь на `messagePump()`:
UIKit отдаёт события только через run loop. Игровой цикл придётся запускать из
UIKit-колбэка (`CADisplayLink`) либо вручную прокручивать run loop внутри
цикла.

**5. Статические плагины OGRE.**

Наш OGRE собран статически (`OGRE_STATIC_LIB` в `OgreBuildSettings.h`), а RoR
читает `plugins.cfg` и вызывает `m_ogre_root->loadPlugin(...)`
(`AppContext.cpp:240-261`) — со статическими плагинами это не работает. В
установленном SDK уже лежит готовый `OgreBites::StaticPluginLoader`
(`include/OGRE/Bites/OgreStaticPluginLoader.h`, `libOgreBitesStatic.a`) —
именно его и надо поставить вместо чтения `plugins.cfg`.

**6. Мелочи, полезные для обвязки.**

- `Build.Shared_LIBS ON` (корневой `CMakeLists.txt:48`) на iOS безвреден:
  `angelscript_addons` и `version_info` объявлены `STATIC` явно.
- Проверка версии зависимостей (`CMakeLists.txt:70-76`) не сработает — файла
  `version.cmake` в нашем префиксе нет.
- iOS попадает в ветку `ELSEIF (UNIX)` (`:111`) → `-std=c++11` и Release-флаги
  `-O2 -ffast-math -DNDEBUG`.
- `OS_LIBS` (`source/main/CMakeLists.txt:459-465`) в ветке `else()` — это
  `X11 -ldl -lrt`, чего на iOS нет; патч `apply_ios_patches.py` уже добавляет
  iOS-ветку с пустым `OS_LIBS`.
- `imgui` лежит в самом дереве (`source/main/gui/imgui/imgui.h`),
  `moFileReader.hpp` и `utf8/*.h` — в `external/header_only/`.
- В `source/main/` 259 единиц трансляции; этого достаточно, чтобы `make -k`
  уложился в таймаут даже при обилии ошибок.

### 7.9. Главная ловушка CMake: `CMAKE_FIND_ROOT_PATH_MODE_* = ONLY`

Это стоило целого прогона. Симптом: конфигурация RoR падала на

```
Could NOT find MyGUI (missing: MyGUI_INCLUDE_DIRS MyGUI_LIBRARIES)
```

при том, что `libMyGUIEngine.a`, `libMyGUI.OgrePlatform.a` и
`include/MYGUI/MyGUI.h` точно лежали в артефакте — это проверял отдельный шаг
«Check the SDK really is complete», и он проходил.

Причина — в `Modules/Platform/Darwin.cmake` самого CMake:

```cmake
list(APPEND CMAKE_FIND_ROOT_PATH "${_CMAKE_OSX_SYSROOT_PATH}")
if(NOT DEFINED CMAKE_FIND_ROOT_PATH_MODE_LIBRARY)
  set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
endif()
if(NOT DEFINED CMAKE_FIND_ROOT_PATH_MODE_INCLUDE)
  set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
endif()
if(NOT DEFINED CMAKE_FIND_ROOT_PATH_MODE_PACKAGE)
  set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
endif()
```

То есть при **любой** кросс-сборке под Apple CMake сам дописывает в
`CMAKE_FIND_ROOT_PATH` путь к sysroot'у и включает режим `ONLY` для
библиотек, заголовков и пакетов. В режиме `ONLY` `find_path`, `find_library`
и `find_package` ищут **только** внутри этого root path, то есть внутри
iPhoneOS SDK. Наш префикс `$IOS_PREFIX`, прописанный в `CMAKE_PREFIX_PATH`,
для них просто не существует.

Отсюда же объяснение, почему в `build-ios-deps.yml` **каждая** зависимость
находилась только тогда, когда её переменные (`OGRE_DIR`, `ZLIB_*`,
`OPENAL_*`, `FREETYPE_*`) задавались вручную: любые поиски, полагавшиеся на
`CMAKE_PREFIX_PATH`, молча не работали.

Спасает то, что установка защищена `if (NOT DEFINED ...)` — значит, определив
эти переменные сами, мы её переопределяем. В `build-ror-ios.yml` теперь:

```
-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=BOTH
-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=BOTH
-DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=BOTH
```

Именно `BOTH`, а не `-DCMAKE_FIND_ROOT_PATH="$IOS_PREFIX"`: во втором случае
префикс становится ещё одним корнем, и CMake начинает отрезать корень от
найденных путей при возврате результата (механика рассчитана на sysroot, к
которому компилятор и так добавляет `-isysroot`). Для нашего префикса это
дало бы неправильные пути. `BOTH` же просто снимает ограничение.

Вдобавок — на всякий случай — вручную задаются переменные модульных находок
(`MyGUI_*`, `PagedGeometry_*`: их ищут собственные
`cmake/find-modules/FindMyGUI.cmake` и `FindPagedGeometry.cmake`) и `*_DIR`
для конфигурационных пакетов (`fmt`, `RapidJSON`, `SocketW`, `Angelscript`).
Плюс `-DCMAKE_DISABLE_FIND_PACKAGE_OpenSSL=TRUE`: SocketW собран без OpenSSL,
и homebrew-овский OpenSSL не должен протечь.

Ещё две меры того же прогона:

- checkout RoR прибит к коммиту
  `d426a5c5e9f76a77a34e5fba8a41cda20edbe97f`. Патчи `apply_ios_patches.py`
  сопоставляются с исходным текстом буквально, поэтому плавающая ветка
  upstream'а однажды сломала бы не сборку, а сам шаг патчинга — поломка куда
  менее очевидная.
- в лог конфигурации печатаются найденные значения `CMAKE_FIND_ROOT_PATH*`,
  `*_DIR`, `MyGUI_*`, `ZLIB_*`, а сам `CMakeCache.txt` уезжает в артефакт,
  чтобы следующая ошибка объясняла себя сама.

---

### 7.10. Перепись ошибок сборки RoR: 287 строк, но всего пять причин

Прогон `38680551775` (1 м 44 с) — первый, который дошёл до самой сборки.
Конфигурация стала полностью зелёной: `Found MyGUI`, `Found PagedGeometry`,
`Found OpenAL`, `Found SocketW`, `Found Angelscript`, затем `Configuring done`
и `Generating done`. Значит, приём из §7.9 действительно вылечил поиск.

`make -k` прогнал 270 единиц трансляции, 137 из них упали, всего 287 строк с
`error:`. Но вся эта масса сводится к **пяти** причинам, каждая — в одном
файле:

| № | Место | Строк | Суть | Исправление |
|---|---|---|---|---|
| 1 | `network/Network.h:49` | 152 | `struct CurlFailInfo` объявлена под `USE_SOCKETW`, а её поле имеет тип `CURLcode` из libcurl, которой у нас нет | закрыть поле `#ifdef USE_CURL` |
| 2 | `audio/SoundManager.h:39` | 106 | `#ifdef __APPLE__` → `<OpenAL/alext.h>`; на iOS это не macOS-фреймворк, а openal-soft с раскладкой `<AL/*.h>` | `#if defined(__APPLE__) && !TARGET_OS_IPHONE` |
| 3 | `scripting/server/ServerScriptCurlHelpers.h:10` | 13 | перечисление `ServerScriptCurlStatusType` спрятано под `#ifdef USE_CURL`, хотя его называют `ServerScriptEngine.h/.cpp`, которые собираются всегда | вынести enum из-под стража |
| 4 | `gui/panels/GUI_RepositorySelector.cpp:1603,1918` и `gui/panels/GUI_MultiplayerSelector.cpp:417` | 13 | три места дёргают libcurl вне стража `USE_CURL` | закрыть `#ifdef USE_CURL` |
| 5 | `utils/ErrorUtils.cpp:49-50`, `utils/PlatformUtils.cpp:202` | 3 | `_L` не определён на iOS, а `system()` на iOS вообще недоступен | см. §7.8 |

Причина №1 объясняет, почему строк так много при такой малой сути:
`Network.h` подключается почти везде, а `__APPLE__`-ветка в `SoundManager.h`
вообще везде, где есть звук.

**Важно, что перепись полная.** В логе нет ни одной строки `too many errors`,
а на одну единицу трансляции приходится 1-5 ошибок (у одной 16) — значит
clang не обрывался на ошибке в заголовке, а доходил и до собственного кода
файла. То есть эти пять причин — действительно всё, что мешало сборке, а не
только её начало.

Отдельно стоит сказать: cURL на iOS **не собирается**, и это официально
поддерживаемая конфигурация RoR (`cmake_dependent_option(ROR_USE_CURL "use
curl" ON "CURL_FOUND" OFF)`). Поэтому причины 1, 3 и 4 — это дыры в
upstream-коде ровно для случая «SocketW настроен, libcurl нет», который у
разработчиков, видимо, никогда не возникал. Мы их не «обезвреживаем», а
закрываем недостающими стражами, так что при будущей сборке cURL те же места
снова скомпилируются как раньше.

Что теряется без cURL: браузер модов (реестр ресурсов) и список серверов по
HTTP. Мультиплеер через SocketW остаётся. Моды при этом можно класть в
`Documents` приложения через «Файлы» — в проекте включён `UIFileSharingEnabled`.

Следующий прогон, `38681835767` (2 м 58 с, сборка впервые дошла до 100%),
подтвердил разбор: **было 287 ошибок, стала одна**. Ею оказался `#include
<alc.h>` в `gui/panels/GUI_GameSettings.cpp:31` — плоская раскладка
заголовков, которая бывает только у macOS-фреймворка OpenAL (его Headers
лежат прямо в путях поиска), а openal-soft, как и `pch.h` с `SoundManager.h`,
использует `<AL/alc.h>`. То есть перепись из §7.10 была полной.

Все исправления лежат в `patches/ror/apply_ios_patches.py` десятью функциями:
`patch_errorutils`, `patch_platformutils`, `patch_appcontext`,
`patch_soundmanager`, `patch_network_header`, `patch_repo_selector`,
`patch_multiplayer_selector`, `patch_server_script_curl_helpers`,
`patch_game_settings_alc` (плюс уже
существовавшие патчи системы сборки). Каждая проверена локально на нетронутых
файлах upstream: якоря совпадают, диффы ровно те, что задуманы.

### 7.11. Линковка: ПРОЙДЕНО. RoR собирается под iOS arm64

Прогон `38684689092` (3 м 3 с) — 270 единиц трансляции, **ноль `error:`**,
**ноль `Undefined symbols`** и, наконец:

```
[100%] Linking CXX executable ../../bin/RoR.app/RoR
[100%] Built target RoR
```

То есть `bin/RoR.app/RoR` — настоящий машинный код под arm64. Причём **ни одна
строка исходников самого RoR для этого не понадобилась**: слинковались ровно
два исправления из `33b8d04`, каждое в своей зависимости.

**Что именно мешало.** Шаг линковки падал с 38 неразрешёнными символами, и все
они сводились к двум причинам:

| Причина | Символов | Откуда | Исправление |
|---|---|---|---|
| Не подключены системные фреймворки | 37 | `libopenal.a`, OGRE (`macUtils`, `OgreFileSystemLayer`), `libOIS.a` (iPhone-бэкенд), libobjc | 9 фреймворков + `-lobjc` в ветку `OS_LIBS` для iOS |
| AngelScript выбрал не те исходники | 1 | `CallSystemFunctionNative` | `ARCHFLAGS` из `CMAKE_OSX_ARCHITECTURES`, а не из `CMAKE_SYSTEM_PROCESSOR` |

37 символов — это `_AudioUnit*`/`_AudioComponent*` (AudioToolbox/CoreAudio),
`_CF*`/`___CFConstantStringClassReference` (CoreFoundation), `_NS*`/
`_OBJC_CLASS_$_*` (Foundation), `_UIAccelerometer`/`_UIApplication`/`_UIScreen`/
`_UIView` (UIKit) и `_objc_msgSend`/`_objc_alloc`/… (libobjc). `-lobjc`
понадобился явно, потому что рантайм Objective-C линкуется автоматически только
когда драйвер компилирует `.mm`, а все свои файлы RoR — обычный C++.

**Отдельная ловушка: `ld` демутирует имена C++.** В диагностике
`CallSystemFunctionNative` напечатан *без* ведущего подчёркивания и с полной
подписью:

```
"CallSystemFunctionNative(asCContext*, asCScriptFunction*, void*, unsigned int*, void*, unsigned long&, void*)"
```

Мой первый разборщик лога искал `^\s+"(_[...])"` и поэтому этот символ
пропустил — то есть я некоторое время считал, что осталось 37 символов и
AngelScript в порядке. Правильный шаблон: `^\s+"(.+?)", referenced from:\s*$`.

**AngelScript: почему хватило `ARCHFLAGS`.** Наличие ссылки на
`CallSystemFunctionNative` само по себе служит доказательством:
`as_callfunc.cpp:567-570` обращается к нему только когда `AS_MAX_PORTABILITY`
**не** определён. Значит `AS_ARM64` был выставлен, значит правку `as_config.h`
делать не надо — не хватало только того, чтобы сборочная система AngelScript
выбрала arm64-исходники. Его `CMakeLists.txt:24` берёт `ARCHFLAGS` из
`CMAKE_SYSTEM_PROCESSOR`, а при кросс-сборке под iphoneos там остаётся значение
хоста, которое не подходит ни под `^arm`, ни под `^aarch64`. Теперь `ARCHFLAGS`
выводится из `CMAKE_OSX_ARCHITECTURES`. Дополнительно шаг проверки в
`build-ios-deps.yml` смотрит `nm -g libangelscript.a` и валит прогон, если
`CallSystemFunctionNative` не *определён*, — молча эта ошибка больше не
проскочит.

Заодно `Built target zip_folder_resources` — значит CMake уже собрал
`bin/resources/*.zip`, включая обязательный `skeleton.zip`.

### 7.12. Платформенный слой: чем `main()` связан с UIKit

Слинкованный бинарник ещё не запускается как приложение: у него точка входа —
обычный `main()`, а UIKit её не инициализировал. Это и есть предмет §7.8, п. 4,
и теперь он реализован тремя новыми файлами в `source/main/ios/` (их создаёт
`patch_ios_platform`, а в цель RoR добавляет `patch_main_cmake`):

| Файл | Что делает |
|---|---|
| `ios_platform.h` | три C-функции: `RoRMain`, `RoR_IosPumpRunLoop`, `RoR_IosGetScreenSize` |
| `ios_main.mm` | настоящий `main()` → `UIApplicationMain`, делегат вызывает `RoRMain` |
| `ios_platform.mm` | `CFRunLoopRunInMode` раз в кадр + размер экрана |

Почему именно так:

1. **Точку входа нельзя оставить в C++.** UIKit требует `UIApplicationMain()`,
   который строит `UIApplication` и запускает цикл приложения, и он **никогда не
   возвращает управление**. Поэтому блокирующий `main()` RoR больше не может
   быть точкой входа: под iOS он переименован в `RoRMain()` и вызывается из
   `-application:didFinishLaunchingWithOptions:`. Так главный цикл RoR остаётся
   ровно тем же, что на десктопе.

2. **Цикл нужно сервировать.** А если точку входа забирает UIKit, то никто не
   крутит его цикл событий. `OgreBites::WindowEventUtilities::messagePump()` на
   iOS — пустышка (`OgreWindowEventUtilities.cpp:174-212`), так что без нас
   касания не приходят вообще, и iOS в конце концов убивает приложение по
   сторожевому таймеру. Поэтому кадровый цикл вызывает
   `RoR_IosPumpRunLoop()` = `CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0, false)`
   — «прокрутить цикл один раз и сразу вернуться»: блокироваться нельзя, мы
   внутри собственного кадрового цикла.

3. **Окно передавать не нужно.** OGRE строит всю иерархию UIKit сам:
   `EAGL2Window` создаёт `UIWindow`, `EAGL2View` и `EAGL2ViewController` и сам
   вызывает `makeKeyAndVisible`, если в `miscParams` нет `externalWindowHandle`
   (`OgreEAGL2Window.mm:188-267`). У RoR на iOS `miscParams` пустой (в него
   что-то кладут только под WIN32), так что условие выполняется. В делегате
   действительно нечего настраивать.

Два места в `SetUpRendering()` пришлось поправить по существу:

- **Плагины OGRE.** Собранный под iOS OGRE — статический, `.dylib`-файлов нет,
  и `Root::loadPlugin()` не сработает. Набор плагинов слинкован в бинарник, и
  его надо зарегистрировать через `OgreBites::StaticPluginLoader`, у которого
  список плагинов вкомпилирован из `OgreBuildSettings.h` (там: система
  отрисовки GLES2 и плагины Octree, PFX, STBI). `plugins.cfg` читать всё равно
  нельзя: CMake кладёт его рядом с `bin/RoR`, а `sys_process_dir` — это
  `bin/RoR.app`.

- **Размер окна отрисовки.** На iOS у системы отрисовки GLES2 нет осмысленной
  опции `"Video Mode"`: разбор даёт 0x0, дальше срабатывает ограничение
  «не меньше 800x600», а следующий за ним цикл «автоопределения режима» дёргает
  `setConfigOption("Video Mode", ...)` **вне try/catch**. Теперь на iOS оба
  шага пропускаются, а размер берётся из `UIScreen.mainScreen.bounds` — в
  точках, длинной стороной в ширину, потому что RoR существует только в
  ландшафте. `EAGL2Window` строит `UIWindow` ровно этого размера.

Мелкие, но важные детали сборочной системы:

- В корневом `CMakeLists.txt` `project()` вызван **без** `LANGUAGES`, то есть
  включены только C и CXX. Добавлен `enable_language(OBJCXX)` — в
  top-level-области и до `add_subdirectory(source/main)`, иначе CMake не сможет
  разобрать исходники `.mm`.
- Переключатель называется `ROR_PLATFORM_IOS`, а **не**
  `OGRE_PLATFORM_APPLE_IOS`. Причина та же, что в `PlatformUtils.cpp`: пока
  `OgrePlatform.h` не подключён, `OGRE_PLATFORM` не определён, а значит равен
  нулю — и сравнение `OGRE_PLATFORM == OGRE_PLATFORM_APPLE_IOS` оказывается
  случайно истинным. Собственный `-D` из CMake от этой ловушки свободен.
- Файлы `.mm` добавляются не в список `SOURCE_FILES`, а через `target_sources`:
  так они не проходят через `expand_file_extensions()` (её регулярное выражение
  разворачивает только `h`/`cpp`) и не попадают в `generate_source_groups()`.

### 7.13. Упаковка: `.app` → `.ipa` без Xcode

Штатный вывод CMake — `bin/RoR.app/RoR`, причём CMake сам сделал бандл, хотя
`MACOSX_BUNDLE` никто не просил. Но `Info.plist` в нём — шаблон
`MacOSXBundleInfo.plist.in`, то есть macOS-ный, а `resources/` CMake кладёт в
`bin/resources`, на уровень **выше** бандла. Для RoR это принципиально:
`SetUpResourcesDir()` считает `sys_process_dir` = каталог исполняемого файла (то
есть сам бандл) и требует, чтобы `resources/` был именно там, а
`SetUpConfigSkeleton()` жёстко требует `resources/skeleton.zip` — без него
запуск не состоится вообще (см. §7.5).

Поэтому шаг «Package RoR.app and IPA» (добавлен в `build-ror-ios.yml`):
копирует `bin/resources` внутрь бандла, пишет поверх macOS-ного iOS-ный
`Info.plist` (только ландшафт, `arm64`, `UIRequiresFullScreen`,
`UIFileSharingEnabled`, `LSSupportsOpeningDocumentsInPlace`), подписывает
бандл ad-hoc (`codesign --force --sign -` — сертификат не нужен, а инструмент
сайдлоада всё равно переподпишет своим бесплатным Apple ID) и заворачивает
`Payload/RoR.app` в zip с именем `.ipa`. Рядом лежит `RoR-ios-ipa`.

### 7.14. Ввод: OIS на iOS есть, но не там, где его ждёт RoR

Три прогона сборки (§7.11–7.13) дали `.ipa`, и следующий вопрос — запустится ли
приложение вообще. Разбор OIS показал, что **оно бы не запустилось**, причём не
из-за платформенного слоя, а из-за ввода. Это самая неприятная находка за всю
работу, потому что она не видна ни в одной сборке: CI линкует, но не запускает.

**Что предлагает iPhone-бэкенд OIS.** `iPhoneInputManager` в
`src/iphone/iPhoneInputManager.mm` — это то, что мы собираем и линкуем
(его символы `_UIAccelerometer`, `_UIDevice`, `_UIScreen` и были среди
неразрешённых 38). Его `freeDeviceList()` отдаёт ровно два устройства:

| Тип | Что это |
|---|---|
| `OISJoyStick` | акселерометр (`iPhoneAccelerometer`) |
| `OISMultiTouch` | мультитач (`iPhoneMultiTouch`) |

**Клавиатуры и мыши там нет вообще.** А RoR в `InputEngine::setup()` создаёт
именно `OISKeyboard`, `OISMouse` и `OISJoyStick`:

```cpp
mKeyboard = static_cast<Keyboard*>(mInputManager->createInputObject(OISKeyboard, true));
mMouse     = static_cast<Mouse*>(mInputManager->createInputObject(OISMouse, true));
```

`iPhoneInputManager::createObject()` на эти типы попадает в `default:` и бросает
`"No devices match requested type."`. Оба вызова в RoR обёрнуты в
`try/catch (OIS::Exception&)` и в `catch` только пишут в лог — то есть
**`mKeyboard` и `mMouse` остаются `nullptr`**, и RoR продолжает работу.

**А дальше — гарантированное падение**, потому что эти указатели нигде не
проверяются:

| Место | Что делает |
|---|---|
| `InputEngine.cpp:657` в `windowResized()` | `mMouse->getMouseState()` |
| `InputEngine.cpp:637-638` в `Capture()` | `mKeyboard->capture()`, `mMouse->capture()` |
| `InputEngine.cpp:664-671` | `ROR_ASSERT(...)` + `->setEventCallback(...)` |
| `InputEngine.cpp:1716` | `mMouse->getMouseState()` |

Хуже всего первое: `windowResized()` вызывается **из конструктора**
(`InputEngine.cpp:434`), а конструктор создаётся при старте. То есть приложение
падало бы сразу.

Отдельная тонкость: `ROR_ASSERT` — это `#define ROR_ASSERT(_EXPR) assert(_EXPR)`
(`Application.h:40`) без каких-либо стражей. Спасало бы `NDEBUG`, и он есть
(`CMAKE_BUILD_TYPE=Release`, `CMAKE_CXX_FLAGS_RELEASE=-O3 -DNDEBUG`), но
`assert` — не защита: строка **после** него всё равно разыменовывает `nullptr`.

**Ещё одна тонкость, которая делает ввод возможным.** `processMouseMotionEvent`,
`processMousePressEvent` и `processMouseReleaseEvent` в RoR **не вызывают
слушателя** — они только пишут в `mouseState`. Слушателя (то есть
`AppContext::mouseMoved`/`mousePressed`/`mouseReleased`, а через него MyGUI)
вызывает сам OIS, изнутри `Mouse::capture()`. На iOS `Mouse` не существует,
значит этот путь мёртв и касания до MyGUI не дошли бы вообще.

**Решение** (`patch_inputengine_header` + `patch_inputengine`):

1. `InputEngine` под iOS наследует `OIS::MultiTouchListener` — слушатель всё
   равно разрешён только один на устройство, так что второй класс не нужен.
2. `setup()` на iOS не идёт по настольному пути, а зовёт `IosSetupInput()`:
   создаёт `OIS::InputManager` в `try/catch` (иначе исключение убьёт процесс),
   берёт акселерометр как джойстик, создаёт `OISMultiTouch` и подписывается на
   него, после чего **всё равно** выполняет `loadConfigFile(-1)` и
   `completeMissingEvents()` — иначе не будет таблицы привязок.
3. Касания переводятся в мышь: `IosHandleTouch()` заполняет `mouseState`
   (`X.abs`/`Y.abs`/`rel`, бит `MB_Left`) и **сам зовёт** слушателя —
   `mouseMoved`/`mousePressed`/`mouseReleased`. MyGUI и камера при этом получают
   ровно то же, что от настольной мыши, и ничего в игровом коде менять не надо.
   Мультитач пока сведён к одному касанию (первое); остальные — на потом.
4. Все настольные разыменования закрыты: `windowResized()`,
   `getMouseNormalizedScreenPos()` и `Capture()` берут состояние из
   собственного `mouseState`, `SetKeyboardListener`/`SetMouseListener`
   запоминают указатель вместо передачи в OIS.

Заодно выяснилось, где именно находятся координаты касаний — это тоньше, чем
выглядит. OIS умножает результат `locationInView()` на `contentScaleFactor`
**своего** `UIView`-делегата (`iPhoneMultiTouch.mm:111`), а `iPhoneInputManager`
берёт этот множитель из своего `mContentScalingFactor`, который остаётся **1.0**,
потому что мы (как и RoR) передаём `"WINDOW"`, но не `"VIEW"`
(`iPhoneInputManager.mm:93,126,153-158`). У OGRE `EAGL2Window` тоже по умолчанию
1.0, и меняется он только параметром `contentScalingFactor`
(`OgreEAGL2Window.mm:63,320`), которого RoR не передаёт, — значит и
`mWidth`/`mHeight` в точках (`OgreEAGL2Window.mm:276`). Обе стороны в точках,
пересчёт не нужен.

Два следствия на будущее. При множителе 1.0 кадр рисуется в масштабе 1x, то
есть на экране 3x будет мыльным; если передать в окно `contentScalingFactor`
ради резкости, соответствие сломается — тогда OIS нужно передать ещё и `"VIEW"`,
иначе касания попадут в треть экрана. И холст получается 844x390 точек, а
интерфейс MyGUI в RoR рассчитан на настольные разрешения, так что меню может не
влезть. Это следующий слой работы, и он не зависит от того, 1x или 3x.

Что это даёт на практике: должен быть виден главный экран, и по нему можно
будет нажимать. Экранных кнопок «газ/тормоз» ещё нет — это следующий слой.

### 7.15. Почему `.ipa` не ставится: три разные причины, и ни одна не в компиляции

Прогон `38687720867` (коммит `a53b961`) собрал всё зелёным, а пользователь
сообщил, что `.ipa` **не устанавливается**. Разбор упаковки и самого файла
показал, что сборка тут ни при чём — дело в трёх отдельных вещах.

**Что подтвердилось в артефакте.** Лог упаковки этого прогона:

```
ror/build/bin/RoR.app/RoR: Mach-O 64-bit executable arm64
ror/build/bin/RoR.app/Info.plist: OK
ad-hoc codesign: OK
bundle carries a _CodeSignature
-rw-r--r--  1 runner  staff  111926089 Oct 15 05:40 RoR-ios.ipa
```

То есть удаление `plugins.cfg` (§7.13) действительно починило подпись: раньше
`codesign` падал с `code object is not signed at all / In subcomponent:
.../RoR.app/plugins.cfg`, теперь он проходит и `_CodeSignature` создаётся.

Разбор самого `.ipa` (адресные загрузки Mach-O) это подтверждает:

| Проверка | Значение |
|---|---|
| `plugins.cfg` внутри бандла | **нет** |
| `_CodeSignature/CodeResources` | **есть** |
| `LC_CODE_SIGNATURE` в бинарнике | **есть** (бинарник подписан) |
| `LC_BUILD_VERSION platform` | **2** — iOS, не macOS |
| `cputype` / `filetype` | `arm64` / `MH_EXECUTE` |

Значит `.ipa` структурно корректный. Искать причину нужно в другом.

**Причина первая: артефакты Actions — это всегда ZIP.** Шаг `upload-artifact`
загружал не только `RoR-ios.ipa`, но и `ror/build/bin/RoR.app/RoR` с
`Info.plist`. GitHub заворачивает артефакт в собственный архив, поэтому с
сайта скачивается `RoR-ios-ipa.zip`, внутри которого лежит `.ipa` **плюс**
посторонние файлы. Отсюда две возможности ошибиться: отдать установщику сам
`.zip` или перепутать файлы внутри. Исправлено с двух сторон:

1. `upload-artifact` теперь берёт **только** `RoR-ios.ipa`.
2. Появился шаг «Publish the IPA as a release asset»: `.ipa` прикрепляется к
   плавающему релизу `ios-alpha`, а файлы релиза отдаются **как есть**, без
   промежуточного архива. Скачать настоящий `.ipa` теперь можно одной ссылкой.
   Для этого workflow'у выдано `contents: write` (было `read`).

**Причина вторая: `MinimumOSVersion 16.0`.** Бинарник собран с
`minos = 16.0`, и в `Info.plist` стояло то же. На телефоне с iOS 15 или
старше система откажется ставить приложение — и это отказ *установки*, не
запуска, поэтому он выглядит как «файл не ставится». Так как `minos` берётся
из `CMAKE_OSX_DEPLOYMENT_TARGET`, цена правки — одна строка в каждом из двух
workflow, но **пересобрать нужно и SDK зависимостей**: все статические
библиотеки в `ios-sdk` несут тот же минимум. Обе переменные переведены на
`13.0` — это все arm64-айфоны (6s и новее) и минимум, который ещё принимает
Xcode 16.

**Причина третья: в `Info.plist` не было экрана запуска.** Ключа
`UILaunchScreen` (или `UILaunchStoryboardName`) не было вовсе. Приложения без
экрана запуска iOS считает «не поддержанными» для текущего экрана и запускает
их в режиме совместимости — с чёрными полями и в чужом масштабе. Добавлен
пустой словарь `UILaunchScreen`, этого достаточно: система берёт фон по
умолчанию, но приложение получает полный экран.

Итого правка одна: `build-ror-ios.yml` и `build-ios-deps.yml` переведены на
`13.0`, в `Info.plist` добавлен `UILaunchScreen`, артефакт сужен до одного
файла, добавлена публикация в релиз.

**Отдельная ловушка: два workflow делили один тег релиза.** `build-ipa.yml` —
это проверка конвейера из этапа 0: она собирает через `xcodegen`/`xcodebuild`
крошечное приложение «Hello World» (`RoRNative.ipa`, 18 КБ) и **на каждый push
в `main` удаляет и заново создаёт релиз `latest-ipa`**. Пользователь смотрит
именно туда — релиз скачивать на телефоне проще всего, — и получает не игру, а
заглушку. Хуже того, если бы настоящий `.ipa` публиковался в тот же тег,
победил бы тот прогон, который закончился позже. Тег поделён: проверка
конвейера теперь живёт в `pipeline-check`, а `latest-ipa` принадлежит
`build-ror-ios.yml` целиком.

Заодно проверено, что сама заглушка `RoRNative.ipa` — **корректное iOS-
приложение** (arm64, `platform = 2`, бинарный `Info.plist`,
`CFBundleSupportedPlatforms = iPhoneOS`, `UILaunchScreen` на месте, подпись
есть). То есть замена содержимого тега `latest-ipa` — это в точности замена
заглушки игрой, структура файла одна и та же.

**Чего этот раздел не говорит.** Остаётся открытым, что именно увидел
пользователь: сообщение установщика не получено. Все три причины выше дают
именно «не устанавливается», но если сообщение окажется другим (например,
установщик не находит устройство), это будет среда Windows-стороны — iTunes
и Apple Mobile Device Support, — а не `.ipa`. Поэтому следующий шаг — точный
текст ошибки.

### 7.16. Заглушка ставится, игра — нет: что именно отличается

Пользователь проверил обе вещи, и это сразу сузило поиск:

| Файл | Результат |
|---|---|
| `RoRNative.ipa` (заглушка, 18 КБ) | **ставится и запускается** |
| `RoR-ios.ipa` (игра, 112 МБ) | ESIGN не ругается, но iOS пишет «Не удаётся установить приложение. Повторите попытку позже» |

Первое важнее второго. Заглушка ставится тем же ESIGN с тем же сертификатом,
значит **сертификат, ESIGN и конвейер рабочие** — и значит дело в нашем
бандле, а не в стороне пользователя. Это отсекает половину гипотез, которые
пришлось бы проверять иначе.

Заглушка — это единственный бандл в репозитории, о котором *известно*, что он
устанавливается, поэтому она стала эталоном для сравнения. Сборка заглушки
(`build_ipa.sh`) делает три вещи, которых наша не делала:

```bash
xcodebuild ... CODE_SIGNING_ALLOWED=NO            # 1. собрать БЕЗ подписи
codesign --force --sign - \
         --entitlements App/Free.entitlements \   # 2. подписать с entitlements
         --generate-entitlement-der "$APP"        # 3. и с DER-формой
```

Причём `Free.entitlements` — **пустой словарь** (см. комментарий в файле:
свободный Apple ID не может нести лишних прав). То есть важен не *состав*
прав, а сам факт, что подпись их несёт и что сгенерирована их DER-форма.
Современная iOS читает DER-права предпочтительно, и подпись без них на
установке отвергается. Наш шаг упаковки делал голый `codesign --force --sign -`
— ни прав, ни DER. Теперь он делает ровно то же, что заглушка, через
`patches/ios/RoR.entitlements`.

Второе отличие — **размер**: 18 КБ против 112 МБ, из которых ~100 МБ это
архивы ресурсов. Ответ iOS «Не удаётся установить… повторите попытку позже» —
это общий отказ `installd`, и на больших приложениях его обычные причины —
нехватка места и неспособность установщика справиться с размером. Отличить это
от проблемы подписи одним действием позволяет второй, маленький `.ipa`: в
упаковку добавлен `RoR-ios-lite.ipa` (~10 МБ) — тот же бандл, тот же
`Info.plist`, та же подпись, но выброшены девять больших архивов ресурсов.
Если ставится он, а полный нет — дело в размере; если не ставится ни один —
дело в подписи. Логика отбора архивов проверена локально: из 26 архивов
остаются ровно те 17, что нужно.

Проверено на сборке `520c080`: бандл теперь действительно несёт то, чем
отличается заглушка, — `Info.plist` стал бинарным (`bplist00`, 27 ключей),
появились `CFBundleSupportedPlatforms = [iPhoneOS]`, `DTPlatformName`,
`DTSDKName`, `PkgInfo`, а `minos` равен 13.0.0, то есть SDK зависимостей
пересобран согласованно.

**Это не догадка, а измерение.** Подпись лежит в бинарнике отдельным блобом
(`LC_CODE_SIGNATURE`, формат — big-endian, в отличие от самого Mach-O).
Разбор этого блоба у заглушки и у нашего `.ipa`:

| Блоб | Заглушка (ставится) | Наш `RoR-ios.ipa` (не ставится) |
|---|---|---|
| `CSMAGIC_CODEDIRECTORY` | есть (1066 Б) | есть (142181 Б) |
| `CSMAGIC_REQUIREMENTS` | есть | есть |
| `CSMAGIC_EMBEDDED_ENTITLEMENTS` (XML-права) | **есть** (372 Б) | **нет** |
| `CSMAGIC_EMBEDDED_DER_ENTITLEMENTS` (DER-права) | **есть** (15 Б) | **нет** |
| `CSMAGIC_BLOBWRAPPER` (CMS) | есть | есть |
| всего блобов | 5 | 3 |

Это единственное структурное различие подписи, и оно ровно того рода, который
iOS проверяет на установке: современные версии читают права именно в DER-виде
и отвергают подпись, где этого блоба нет. Сборка до `e3b1f5d` подписывала
голым `codesign --force --sign -`, поэтому в её подписи было три блоба вместо
пяти. Теперь шаг упаковки подписывает так же, как заглушка, и печатает в лог
разбор своей подписи (`codesign -d --entitlements :-` и `codesign -dvvv`),
чтобы это было видно без скачивания.

---

## Что всё ещё неизвестно / не проверено

- **Собирается ли платформенный слой и запускается ли приложение.** Пункты
  §7.11–7.13 — это код и шаги сборки; проверены они пока только локально
  (якоря всех 13 функций-патчей совпадают, три новых файла создаются, все три
  workflow разбираются). Что они компилируются и линкуются и, главное, что
  приложение не падает при старте — не проверял ни один прогон.
- **Сколько времени занимает инициализация.** Делегат блокируется внутри
  `-application:didFinishLaunchingWithOptions:` на весь `RoRMain`, а RoR при
  старте читает конфиги, сканирует моды и компилирует скрипты. Сторожевой
  таймер iOS убивает приложение, которое не «отвечает» слишком долго; уложится
  ли инициализация в его окно — открытый вопрос.
- **Слой ввода.** Решено: `touch` → `EV_*` внутри `InputEngine`, интерфейс
  движка не меняем. Но в upstream нет ни `pushEvent`, ни очереди событий, ни
  мультитача: `main.cpp` каждый кадр дёргает `Capture()`, а игра опрашивает
  состояние через `getEventValue()`. Значит, iOS-слой должен наполнять
  `mouseState`/`keyState`/`joyState` напрямую (или синтезировать
  `OIS::MouseEvent`/`OIS::KeyEvent` и звать существующие `process*`). Плюс
  конструктор `InputEngine` на iOS падает: `setup()` → `createInputSystem()`
  бросает исключение без окна, а `windowResized()` в конструкторе
  (`InputEngine.cpp:434`) зовёт `mMouse->getMouseState()` (`:657`) без проверки.
  Ещё не сделано.
- **Как рисовать экранные кнопки.** Не выбрано: MyGUI или собственный оверлей
  OGRE.
- **Совместимость сохранений и модов.** Пути уже сведены в
  `~/Documents/RigsOfRods` (§7.8), но что именно из содержимого `Documents`
  реально подхватится, не проверено.

### 7.17. Первая попытка подписать с entitlements уронила подпись совсем

Первый вариант `patches/ios/RoR.entitlements` (`fcea5d8`) выглядел логично:
пустой словарь + подробный комментарий, объясняющий, зачем файл нужен. Но
в комментарий была вставлена командная строка с `--force --sign --entitlements
--generate-entitlement-der`. В XML **запрещена последовательность из двух
дефисов внутри комментария**. Парсер entitlements в macOS отвергает весь
файл целиком:

```
ad-hoc codesign: FAILED (not fatal, the sideloader re-signs)
Failed to parse entitlements: AMFIUnserializeXML: syntax error near line 9
bundle is unsigned (expected; the sideloader adds the signature)
----- what the signature actually says -----
ror/build/bin/RoR.app: code object is not signed at all
```

Последствие: бандл уехел в релиз **полностью без подписи** — `.ipa` стала
на 145 КБ меньше (144 994 байта ≈ размер пропавшей подписи). Это хуже, чем
прежняя голая подпись с тремя блобами: iOS получает вообще неподписанный
bundle.

Причина подтверждена локально, на Windows, без macOS: Python `plistlib`
использует expat — стандартный XML-парсер:

```
OURS  (broke codesign) -> ExpatError: not well-formed (invalid token): line 9, column 3
STUB (works)           -> {}   (Free.entitlements заглушки парсится нормально)
```

Файл переписан без двойных дефисов в комментарии, добавлено предупреждение
о ловушке. Парсится в пустой словарь `{}`, как и `Free.entitlements` заглушки.

**Шаг упаковки усилен**, чтобы это никогда не повторилось молча:
- `plutil -lint` проверяет entitlements-файл до использования, при отказе — подпись без него.
- Функция `adhoc_sign` при неудаче с entitlements пробует подписать **без** них, чтобы не уйти с полностью неподписанным бандлом.
- Возвращает ненулевой код, если и это не удалось, что останавливает шаг; upload всё равно сработает (`if: always()`).

Оба бандла (полный и lite) идут через `adhoc_sign`. Логика протестирована
локально с заглушкой `codesign`: приняты entitlements → первый путь; отказ
entitlements → fallback; оба варианта упали → UNSIGNED; `plutil` отвергает
файл → fallback.

### 7.18. Подпись называла бандл не тем именем; добавлена третья (tiny) сборка

Разбор лога успешного запуска `38698653075` дал главное, что не было видно
снаружи: строку, которую печатает сам codesign.

```
Executable=/Users/runner/work/ror-ios/ror-ios/ror/build/bin/RoR.app/RoR
Identifier=RoR-5555494495c14ca205fb3d8ab572baf55691bc9e
Format=bundle with Mach-O thin (arm64)
CodeDirectory v=20400 size=142309 flags=0x2(adhoc) hashes=4436+7 location=embedded
```

`LC_UUID` этого же бинарника — `95c14ca205fb3d8ab572baf55691bc9e`. То есть
подпись получила имя `<CFBundleExecutable>-<LC_UUID>`. Это внутренний fallback
codesign на случай, когда `CFBundleIdentifier` из бандла получить не удалось.
Единственный бандл, который на устройстве ставится — заглушка milestone-0 —
несёт `Identifier=io.github.ror.ios`, то есть настоящий bundle id. iOS
сопоставляет подпись и plist при проверке, поэтому расхождение — правдоподобная
причина «не удаётся установить».

Обе подписи разобраны побайтово (Python, без macOS, скрипты
`sigver.py`/`codesig.py`/`macho2.py`). **Совпадает** почти всё: `hashType`
SHA-256, версия CodeDirectory `0x20400`, `flags=0x2` (adhoc), пять блобов
(`CODEDIRECTORY`, `REQUIREMENTS`, `EMBEDDED_ENTITLEMENTS` xml,
`EMBEDDED_DER_ENTITLEMENTS`, `BLOBWRAPPER`), DER-полезная нагрузка
`7005020101b000`, pageSize 4096, nSpecialSlots 7; Mach-O `MH_EXECUTE`,
сегменты `__PAGEZERO/__TEXT/__DATA_CONST/__DATA/__LINKEDIT`, `__LINKEDIT`
последний, `LC_CODE_SIGNATURE` в конце файла, файл целиком валиден по zip
(56 записей, дублей нет, странных имён нет, zip64 нет, симлинков нет).

**Отличается** ровно два пункта:
- `Identifier` — см. выше;
- размер XML-entitlements: 1474 байта против 372 у заглушки. Причина чисто
  косметическая: комментарий в `RoR.entitlements` был на 29 строк.

Также отличаются состав `LC_*` и набор dylib (у нас `LC_DYLD_INFO_ONLY`,
`LC_DYLIB_CODE_SIGN_DRS`, 13 обычных dylib и нет `LC_BUILD_VERSION`; у заглушки
`LC_DYLD_EXPORTS_TRIE`/`LC_BUILD_VERSION`, 10 weak-загрузок Swift и
`LC_RPATH`), но это следствие того, чем собран бинарник, и установке не мешает.

Что сделано:
- в оба вызова `codesign` добавлено `--identifier $BUNDLE_ID` — подпись теперь
  несёт тот же идентификатор, что plist, независимо от того, что сбивает
  определение бандла внутри codesign;
- комментарий в `RoR.entitlements` сокращён: блоб XML-entitlements стал 919
  байт против 1474 и против 372 у заглушки. Остаток — всё ещё текст
  комментария, на подпись он не влияет, но длиннее заглушечного без пользы;
- в лог добавлена диагностика: та же подпись на копиях бандла с plist в XML, в
  бинаре и с `--identifier`. Результат (запуск `38826822306`):

  ```
  plist says: org.rigsofrods.ror-ios
  xml    -> Identifier=org.rigsofrods.ror-ios
  bin    -> Identifier=org.rigsofrods.ror-ios
  forced -> Identifier=org.rigsofrods.ror-ios
  ```

  То есть на **урезанном** бандле (только бинарник, `Info.plist`, `PkgInfo`)
  codesign определяет bundle id правильно в любом из трёх вариантов. Значит,
  прежнее `RoR-<LC_UUID>` вызывал не формат plist и не отсутствие
  `--identifier`, а что-то в полном бандле — в нём были ещё `resources/` и
  `_CodeSignature/`. Причину это не снимает, но `--identifier` делает
  результат детерминированным и совпадающим с заглушкой независимо от неё.
  Проверено на выпущенном файле: `CodeDirectory identifier:
  org.rigsofrods.ror-ios` у всех трёх `.ipa`;
- добавлен третий, диагностический `.ipa` — `RoR-ios-tiny.ipa`: тот же
  `Info.plist`, тот же `PkgInfo`, тот же вызов `codesign` и та же сборка
  `Payload/` + zip, но исполняемый файл ~50 КБ вместо 18 МБ.

Смысл tiny: если он ставится, а `RoR-ios-lite.ipa` — нет, то упаковка, plist и
подпись доказанно исправны, и дело в самом бинарнике RoR (размер/структура).
Если не ставится ни один — дело в упаковке или в подписывальщике, а не в игре.

Чего до сих пор нет: **текста ошибки со стороны устройства**. Симптом
(иконка появляется, установка обрывается на середине) указывает либо на
нехватку места, либо на провал проверки подписи после распаковки, и различить
их можно только по логу `installd` с самого телефона.

### 7.19. Диагностический tiny-бандл **ставится и запускается**

Пользователь установил `RoR-ios-tiny.ipa` (8 954 байта; в бандле пять файлов,
исполняемый файл 89 104 байта) и увидел зелёный экран. Это первый успешный
запуск за всю цепочку установки, и он снимает сразу несколько подозрений:

- упаковка `.ipa` (`Payload/RoR.app/...` + zip) исправна;
- `Info.plist` — те же 27 ключей, что и у полной сборки — принимается
  установщиком;
- вызов `codesign` с `--identifier`, entitlements и DER-формой даёт подпись,
  которая проходит проверку на устройстве;
- установщик (ESIGN) и его сертификат работают.

Значит, из различий между tiny и `RoR-ios-lite.ipa` виновато ровно одно из
двух:

- сам исполняемый файл RoR — 17.48 МБ против 89 КБ;
- файлы ресурсов — в lite их 35 штук (10.5 МБ), в tiny ни одного.

Аудит ресурсов (`res_audit.py`) ничего не нашёл: нет совпадений имён без учёта
регистра, нет не-ASCII имён, нет имён с хвостовым пробелом или точкой, нет
нулевых файлов, максимальная глубина 6, режимы 0644 плюс один 0755 на сам
бинарник, методы zip — stored/deflate. Общий набор у lite и full — 36 файлов,
28.0 МБ несжатыми, из которых 17.48 МБ это сам бинарник. То есть «плохого
файла» в ресурсах не видно.

Следующий шаг: проверить `RoR-ios-lite.ipa` из **текущего** релиза (тот, что с
исправленным идентификатором — в прошлый раз проверялся ещё старый файл). Если
он ставится, правки подписи хватило. Если нет — вторая диагностическая сборка
`RoR-ios-bin.ipa`: настоящий бинарник RoR без каталога `resources/`. Она
замыкает матрицу 2×2 (бинарник × ресурсы) и однозначно указывает виновника.

### 7.20. Бинарник невиновен: виноваты `resources/`. Матрица 2×2 замкнута

`RoR-ios-lite.ipa` из текущего релиза (исправленный идентификатор) на телефоне
так и не установился. Зато `RoR-ios-bin.ipa` — **тот же самый 17.48 МБ бинарник
RoR**, но без каталога `resources/` (в бандле всего четыре файла) — **ставится и
запускается** (чёрный экран, что ожидаемо: контента нет). Итоговая матрица:

| Сборка | Исполняемый файл | `resources/` | Результат на устройстве |
|---|---|---|---|
| `RoR-ios-tiny.ipa` | 89 КБ (заглушка) | нет | **ставится, зелёный экран** |
| `RoR-ios-bin.ipa` | 17.48 МБ (RoR) | нет | **ставится, чёрный экран** |
| `RoR-ios-lite.ipa` | 17.48 МБ (RoR) | 35 файлов | **не ставится** |
| `RoR-ios.ipa` | 17.48 МБ (RoR) | 44 файла | **не ставится** |

Отсюда однозначно: упаковка, `Info.plist`, `codesign`, установщик и **сам
бинарник RoR исправны**. Установку ломает присутствие каталога `resources/`.
Причём это не про размер: пользователь подтверждает, что подпись и установка
крупных файлов у него работают, а `resources/` в lite — всего 10.5 МБ.

Важное независимое подтверждение — лог подписывальщика (ESIGN):

```
Payload готов (файлов: 36)
zsign: Подписание завершено            (0.11s)
Результат: RoR-ios-lite 4_signed_*.ipa (18 МБ)
Установка зарегистрирована f5b0fdd6...
-> "все равно тоже самое не устанавливается"
```

То есть подпись (`zsign`) отрабатывает успешно и до конца; падает уже установка
(`installd`) при наличии ресурсов. Это согласуется с матрицей и ещё раз снимает
версию «проблема в подписи».

Что проверено и исключено по ресурсам: CRC внешнего zip и всех внутренних
архивов — чисто; методы stored/deflate корректны; нет файлов нулевой длины, нет
не-ASCII имён, нет совпадений имён без учёта регистра, максимальная глубина 6,
режимы 0644 (и один 0755 на бинарник). «Плохого файла» статически не видно.
Также отвергнута версия про минимальную версию ОС: `LC_BUILD_VERSION` у RoR и у
tiny одинаков (platform=2, minos 13.0.0), проверено через macholib.

Следующий шаг — **бисекция ресурсов**. Полный набор lite делится пополам по
верхнеуровневым элементам каталога `resources/` (17 zip + каталог
`managed_materials/` = 18 элементов; середина на 9). Получаются две сборки
`RoR-ios-lite-h1.ipa` / `RoR-ios-lite-h2.ipa` с настоящим бинарником. Правило
теста: если `h1` не ставится — виновник в первой половине, если ставится — во
второй (тогда проверяется `h2`). Проигравшую половину делим снова. Цель —
найти минимальный набор файлов, после которого установка `installd` срывается.

Тонкость: первая попытка делила не тот каталог (`ror/build/bin/RoR.app/resources`
— ресурсы полной сборки), из-за чего половины вышли 28.7 МБ и 73.2 МБ. Теперь
делится именно `LiteStage/Payload/RoR.app/resources`.

Чтобы не путать «не устанавливается» и «устанавливается, но падает при
старте», добавлена третья диагностическая сборка
`RoR-ios-tinyres.ipa`: проверенный tiny-бинарник (89 КБ, ставится и даёт зелёный
экран) плюс **весь** набор ресурсов lite. Если он не установится — ресурсы ломают
установку сами по себе; если установится и покажет зелёный экран — ресурсы
исправны, и настоящий бинарник RoR не может стартовать, когда видит их.

Отдельно устранена известная опасность, не связанная с установкой: делегат
вызывал `RoRMain()` прямо из `-application:didFinishLaunchingWithOptions:` и
блокировал этим запуск навсегда (watchdog `0x8badf00d`). Теперь `RoRMain()`
запускается на следующем тике главного run loop через `dispatch_async`, а
`-didFinishLaunching...` возвращается сразу. Это может проявляться как
«приложение ставится и мгновенно закрывается».

### 7.21. Не размер и не бинарник: хватает **одного файла в `resources/`**. Под подозрением — наша подпись

Самый узкий тест: `RoR-ios-res0.ipa` — это `bin` плюс **один файл на 4 байта**
`resources/rotest.txt`. Разница с рабочим `bin` — **465 байт**. Установщик на
телефоне отвечает системным диалогом «Не удается установить приложение
«Rigs of Rods»» / «Повторите попытку позже.»:

| Сборка | Отличие от `bin` | Результат |
|---|---|---|
| `RoR-ios-bin.ipa` | — | **ставится** |
| `RoR-ios-res0.ipa` | `+resources/rotest.txt` (4 Б) | **не ставится** |
| `RoR-ios-res1.ipa` | `+resources/skeleton.zip` (61 КБ) | **не ставится** |
| `RoR-ios-tinyres.ipa` | tiny-бинарник + все ресурсы lite | **не ставится** |

465 байт исключают размер полностью и запирают причину на «в бандле есть хоть один
файл внутри подкаталога `resources/`».

Дальше — разгадка. Сравнение `_CodeSignature/CodeResources` у `bin` и `res0`
показало, что **`codesign` строит разные правила ресурсов** в зависимости от того,
есть ли в бандле файлы ресурсов:

- `bin` (файлов ресурсов нет): `rules` = `^.*` — «всё есть ресурс»; `files2` пуст.
- `res0`/`res1` (есть): `rules` = `^Resources/` (с большой буквы) и `rules2` с
  правилом `^[^/]+$ -> nested`. То есть любой верхнеуровневый элемент бандла
  трактуется как **вложенный код**, а наш каталог называется `resources/`
  (с маленькой) и под правило `^Resources/` не попадает.

То есть наша ad-hoc подпись для «ресурсных» бандлов получается неканоничной.
Разбор исходников `zsign` (подписант внутри ESIGN) показал, что он **сохраняет уже
существующий `_CodeSignature/CodeResources`**, если считает, что ничего не
менялось (ветки `!bForceSign` и `else if (jvNode.has("changed"))` в
`ZBundle::SignNode`), а свои правила пишет как `^.*` — ровно как у рабочего `bin`.
Если ESIGN идёт по ветке «сохранить», на телефон уезжает именно **наша** подпись:
она проходит для `bin` и ломается для `res0`.

Чтобы решить это прямо, поставлены четыре узких сборки:

| Сборка | Что проверяет |
|---|---|
| `RoR-ios-file0.ipa` | `bin` + файл `rotest.txt` **в корне** бандла (без нового каталога) |
| `RoR-ios-res0c.ipa` | `bin` + файл в каталоге `Resources/` (с большой буквы) |
| `RoR-ios-res0u.ipa` | как `res0`, но **вовсе без нашей подписи** |
| `RoR-ios-liteu.ipa` | весь lite **без нашей подписи** |

Правило чтения: если ставятся `res0u` и `liteu` — виновата наша ad-hoc подпись, и
лечение простое: отдавать установщику **неподписанный** бандл (iOS всё равно не
принимает ad-hoc подпись, подписывать обязан установщик). Если ставится `res0c`, а
`res0` нет — дело в регистре имени каталога и правилах `^Resources/`. Если
ставится `file0`, а `res0` нет — дело именно в подкаталоге, а не в лишнем файле.

Для протокола закрыт и более ранний ложный след — «битый при скачивании» файл.
Доказано, что часть попыток шла с **обрезанным** скачиванием: ESIGN сообщал
`Truncated input file (needed 6729105, only 6714149 available)`, а `tinyres`
(11 038 794 Б вместо 11 044 634 Б) заканчивался встроенным `PK\x05\x06` из
сохранённого `particles.zip`, из-за чего libarchive видел 7 имён `particles.zip`
верхним уровнем архива. На сервере все файлы целы (размер и SHA-256 совпали при
повторной проверке), поэтому раздача перенесена на локальный HTTP-сервер с ПК,
чтобы исключить обрезание.

---

### 7.22. Разгадка: виновато **имя каталога** `resources`. `codesign` меняет правила ресурсов

Проверка узких сборок (на этот раз каждая ставилась **после перезагрузки телефона**,
чтобы исключить залипшее состояние установщика):

| Сборка | Лишний элемент | Наша подпись | Установка и запуск |
|---|---|---|---|
| `RoR-ios-file0.ipa` | `rotest.txt` в корне | есть | **ставится** |
| `RoR-ios-res0c.ipa` | `Resources/rotest.txt` | есть | не ставится |
| `RoR-ios-res0u.ipa` | `resources/rotest.txt` | **нет** | не ставится |
| `RoR-ios-liteu.ipa` | все ресурсы | **нет** | не ставится |
| `RoR-ios-binu.ipa` | — | **нет** | **ставится** |

Ключ — последняя строка. `binu` **без подписи ставится**, значит неподписанный бандл
установщик подписывает нормально, и «нет подписи» само по себе не приговор. А `res0u`
(без подписи, но с `resources/`) — не ставится. Значит причина не в подписи.

Следующий набор разделил три оставшихся версии: сам каталог, файл внутри него или имя
каталога.

| Сборка | Что добавлено к `bin` | `rules` в `CodeResources` | Установка |
|---|---|---|---|
| `bin` | — | `^.*` | **ставится** |
| `file0` | `rotest.txt` в корне | `^.*` | **ставится** |
| `zzz0` | `zzz/rotest.txt` | `^.*` | **ставится** |
| `lproj0` | `Base.lproj/rotest.txt` | `^.*` | **ставится** |
| `flat0` | `skeleton.zip` в корне | `^.*` | **ставится** |
| `d0` | **пустой** `resources/` | `^Resources/` | (не проверялось на телефоне) |
| `res0` | `resources/rotest.txt` | `^Resources/` | не ставится |
| `res0c` | `Resources/rotest.txt` | `^Resources/` | не ставится |

Версия «подкаталог вообще нельзя» мертва: `zzz0` — это ровно подкаталог с файлом, и он
ставится и запускается. Отличие `zzz0` от `res0` — **только имя каталога**.

Версия «виновата наша подпись» тоже мертва по существу, но подпись оказалась
**индикатором**: стоит в бандле появиться каталогу с именем `resources` (регистр не
важен — `Resources` срабатывает так же, что видно по правилам у `res0c`), и `codesign`
переключает **правила ресурсов** бандла на «макосный конверт»:

- без такого каталога → `rules` = `^.*` (плюс штатные `*.lproj`), в `rules2` нет
  правила `nested`. Такую подпись iOS принимает.
- с таким каталогом → `rules` = `^Resources/` и производные. Такую подпись iOS не
  принимает, и установка падает с системным диалогом «Не удается установить
  приложение «Rigs of Rods»» / «Повторите попытку позже.»

**Лечение: каталог ресурсов внутри бандла называется `rorres`.** RoR зашивает имя
`resources` ровно в четырёх местах, всё остальное идёт через `sys_resources_dir`:

- `AppContext.cpp:559` — `PathCombine(sys_process_dir, "resources")` (центральное);
- `ContentManager.cpp:296,298,300` — `PathCombine("resources", "beamobjects.zip" |
  "dashboards.zip" | "gadgets.zip")`.

`apply_ios_patches.py` переписывает все четыре на `rorres` (функция
`patch_appcontext` и новая `patch_contentmanager`), а шаг упаковки кладёт архивы в
`RoR.app/rorres/`. Каталог `content` (моды) правило не задевает, переименовывать его
не нужно.

Осталось подтвердить на телефоне парой узких сборок: `RoR-ios-res0r.ipa` (`bin` +
`rorres/rotest.txt` — тот же файл, каталог переименован) должен ставиться, а затем
ставить настоящий `RoR-ios-lite.ipa` (ресурсы уже в `rorres/`) и запускать игру.

### 7.23. Zip-архивы: `skeleton.zip` не открывался, потому что OGRE собрался без `OgreZip.cpp`

Первая сборка, дошедшая до запуска на телефоне, упала сразу после
`startup: OGRE is up and the render window was created`. В трассе нет ни строки
`Added resource location ... of type 'Zip'`, зато есть

```
 27: [trace] ArchiveFactory for archive type FileSystem registered.
```

и **нет** строки `ArchiveFactory for archive type Zip registered.` — то есть
фабрики Zip-архивов в приложении не было вовсе.

Причина — в самом OGRE (`Ogre/CMakeLists.txt:375`):

```cmake
cmake_dependent_option(OGRE_CONFIG_ENABLE_ZIP, TRUE, "ZZip_FOUND", FALSE)
```

Вся поддержка zip (`OgreMain/src/OgreZip.cpp`, `ZipArchiveFactory`) подключается
**только если найден zziplib**. Не нашли — опция молча превращается в `FALSE`,
файл не компилируется, а в `OgreBuildSettings.h` попадает

```c
#define OGRE_NO_ZIP_ARCHIVE 1
```

RoR же читает из zip ровно то, без чего не стартует: `skeleton.zip` (базовые
конфиги) и далее `meshes.zip`, `textures.zip` и прочие архивы ресурсов. Поэтому
приложение и падало.

Проверка на выпущенном бинарнике (`Payload/RoR.app/RoR`):

| Строка | Вхождений |
|---|---|
| `ZipArchiveFactory` | 0 |
| `zzip_dir_open_ext_io` | 0 |
| `Modification of zipped archives is not supported` | 0 |
| `FileSystemArchiveFactory` | 11 |
| `Cannot find an archive factory` | 1 |

#### Первая попытка (не сработала): подсказать `FindZZip` значениями кэша

Собрали zziplib сами и передали OGRE `-DCMAKE_PREFIX_PATH`, `-DZZIP_HOME`,
`-DZZip_INCLUDE_DIR`, `-DZZip_LIBRARY_REL`. Прогон `38860350005` показал, что
zziplib собрался и установился (`libzzip-0.a` → `lib/libzzip.a`,
`include/zzip/*.h`), но OGRE всё равно написал в `ogre-configure.log`:

```
-- Looking for ZZip...
-- ZZip_PREFIX_PATH changed.
-- Checking for module 'zziplib'
--   Package 'zziplib' not found
-- Could not locate ZZip
```

Причина — в штатном `OGRE/CMake/Packages/FindZZip.cmake`:

```cmake
clear_if_changed(ZZip_PREFIX_PATH
    ZZip_INCLUDE_DIR
    ZZip_LIBRARY_REL
    ...)
```

`clear_if_changed` сравнивает текущее `ZZip_PREFIX_PATH` с сохранённым в кэше
`ZZip_PREFIX_PATH_LAST`. В свежем каталоге сборки сохранённого значения нет,
поэтому **первый же** `configure` считает переменную изменившейся и стирает
подсунутые `-DZZip_*`. Дальше `find_path`/`find_library` ищут только внутри
путей кросс-компиляции и `$IOS_PREFIX` не видят.

#### Что сработало: подменить `FindZZip.cmake`

Правильный ответ — не подсказывать значения, а **заменить модуль поиска**: новый
шаг workflow `Pin FindZZip to the zziplib in the SDK prefix` пишет
`ogre/CMake/Packages/FindZZip.cmake` с тем, что OGRE реально читает
(`ZZip_FOUND`, `ZZip_INCLUDE_DIRS`, `ZZip_LIBRARIES`), и предварительно
проверяет, что `libzzip.a` и `include/zzip/zzip.h` на месте. Флаги
`-DCMAKE_PREFIX_PATH` / `-DZZIP_HOME` / `-DZZip_*` из конфигурации OGRE убраны:
они не только бесполезны, но и маскируют проблему.

`include(CMake/Dependencies.cmake)` вызывается в `Ogre/CMakeLists.txt:301`, то
есть **до** строки 375, поэтому `ZZip_FOUND TRUE` из подменённого модуля доводит
`OGRE_CONFIG_ENABLE_ZIP` до `TRUE` и `OgreZip.cpp` попадает в сборку.

#### Проверка

Прогон `38860975606` — все шаги OGRE зелёные, включая
`Assert OGRE has Zip archive support (OGRE_NO_ZIP_ARCHIVE 0)`. В скачанном
`ios-sdk` (126 776 413 байт):

| Проверка | Результат |
|---|---|
| `include/OGRE/OgreBuildSettings.h` → `#define OGRE_NO_ZIP_ARCHIVE 0` | есть |
| `include/zzip/zzip.h` | есть |
| `lib/libzzip.a` (и `libzzip-0.a`) | есть |
| `libOgreMainStatic.a` → `ZipArchiveFactory` | 52 |
| `libOgreMainStatic.a` → `Modification of zipped archives is not supported` | 1 |
| `CMake/OgreTargets.cmake` → `INTERFACE_LINK_LIBRARIES` у `OgreMain` | `.../ios-sdk/lib/libzzip.a;$<LINK_ONLY:ZLIB::ZLIB>` |

Последняя строка важна: RoR линкуется с **импортированной** целью `OgreMain`,
поэтому путь к `libzzip.a` доезжает до линковки сам, а workflow затем
переписывает абсолютные пути сборочной машины (`sed` по `*.cmake`).

Побочный вывод: это ошибка того же класса, что и `resources` в §7.22 — **OGRE
молча отключает функциональность**, если зависимость не найдена. Значит мало
проверять «собралось», надо проверять, что нужная опция сборки включена.

### 7.24. Zip заработал. Причина падения на старте — вьюпорт без камеры (баг OGRE)

Первая же сборка с починенным Zip (`ror-zipfix.ipa`, коммит `78fa9ff`) прошла
дальше, чем все предыдущие:

```
 28: ArchiveFactory for archive type Zip registered.          <- появилась
209: Added resource location '.../rorres/skeleton.zip' of type 'Zip' to resource group 'SrcRG'
210: SetUpConfigSkeleton: skeleton.zip opened as a Zip archive
220: SetUpConfigSkeleton: finished
```

`SetUpConfigSkeleton()` теперь отрабатывает целиком, то есть `skeleton.zip`
действительно открывается как Zip. Падение уехало в другой код — и теперь у
него есть символизируемый след (обработчик сигнала печатает `si_addr`, `PC`,
`LR` и `FP`, все — смещениями от базы исполняемого файла):

```
[crash] SIGSEGV - invalid memory access addr=0x130 pc=0x…(+0x5bcc68) lr=0x…(+0x513514)
```

Оба запуска одного и того же бинарника падают по **одинаковым** смещениям,
то есть это воспроизводимо, а не гонка. По таблице символов (`ror_sym.py`):

| Регистр | Смещение | Символ |
|---|---|---|
| `PC` | `+0x5bcc68` | `Ogre::Frustum::setAspectRatio(float) + 0x0` |
| `LR` | `+0x513514` | `-[EAGL2View layoutSubviews] + 0xb4` |
| `si_addr` | `0x130` | `NULL + offsetof(Frustum, mAspectRatio)` |

Читается однозначно: `-[EAGL2View layoutSubviews]` вызывает
`setAspectRatio()` на **нулевом** указателе. `PC` попал ровно в первое
инструкцию `setAspectRatio`, а адрес сбоя `0x130` — это «ноль плюс поле
структуры»: значит вызов уже развёрнут компилятором (не виртуальный, иначе
сбой был бы на загрузке vtable), а `this` равен `NULL`.

Почему камера нулевая — это две штатные строчки самого RoR:

```cpp
source/main/AppContext.cpp:432
    // Create viewport (without camera)
    m_viewport = m_render_window->addViewport(/*camera=*/nullptr);
```

RoR **намеренно** создаёт вьюпорт без камеры (чтобы сразу задать чёрный фон) и
прицепляет камеру сильно позже:

```cpp
source/main/gfx/camera/CameraManager.cpp:123
    App::GetAppContext()->GetViewport()->setCamera(m_camera);
```

А OGRE 1.11.6 в `RenderSystems/GLES2/src/EAGL/OgreEAGL2View.mm` проверяет
**количество** вьюпортов, но не саму камеру:

```objc
if(window->getNumViewports() > 0)
{
    Ogre::Viewport *viewPort = window->getViewport(0);
    viewPort->getCamera()->setAspectRatio((Real) width / (Real) height);  // камеры нет
}
```

`layoutSubviews` вызывает UIKit на первом же проходе run loop. RoR же качает
run loop на каждой стартовой засечке (`CFRunLoopRunInMode` в
`RoR_IosStartupTick`), поэтому вызов прилетает именно в тот промежуток, когда
вьюпорт уже есть, а камеры ещё нет. Отсюда и «странность» из первой версии
следа: в одном запуске последней засечкой была `SetUpConfigSkeleton: finished`,
в другом — `OGRE is up…`; место сбоя одно и то же, различается лишь то, на
каком проходе run loop UIKit решил разложить вид.

Косвенное подтверждение: `windowMovedOrResized()` (строка выше) отработал
успешно — он камеру не трогает. Значит защиты требует только `setAspectRatio`.

**Исправление** — в workflow зависимостей, сразу после клона OGRE: точечная
замена единственной строки на проверку камеры:

```objc
if(viewPort != NULL && viewPort->getCamera() != NULL)
{
    viewPort->getCamera()->setAspectRatio((Real) width / (Real) height);
}
```

Заодно поправлен косметический дефект обработчика: `ror_trace_hex()` сам
дописывает `0x`, а префиксы передавали его ещё раз, из-за чего в следе
печаталось `pc=0x0x…`, `lr=0x0x…`, `(+0x0x…)`.

Побочный вывод: это уже третий случай одной и той же породы (§7.22 —
`resources`, §7.23 — `OgreZip.cpp`, §7.24 — камера). iOS-путь OGRE и упаковка
бандла рассчитаны на «идеального» потребителя; RoR таким не является, и
каждое такое допущение приходится либо подпирать патчем, либо проверять
явно.

---

### 7.25. Чёрный экран вместо падения: RoR глотает исключение, а MyGUI ищет шейдеры под чужими именами

Патч §7.24 отработал: приложение перестало падать. Оно стало **запускаться с
чёрным экраном**, оставаясь в списке запущенных бесконечно. Такой симптом —
это не зависание, а возврат из `RoRMain`: весь цикл кадров упакован в
`try/catch`, исключение внутри него логируется и **глотается**, после чего
функция доходит до `return 0`. На iOS возврат из `RoRMain` не завершает процесс
(`didFinishLaunchingWithOptions` вернулся уже давно), главный run loop
продолжает крутиться с пустой очередью — и приложение вечно живёт на чёрном
окне, без падения и без watchdog.

Чтобы это доказать, а не гадать, в оба `catch` и перед общим `return 0`
добавлен вызов `RoR_IosShowFatal()`: одна небуферизованная строка `[fatal]` в
`ror-trace.log` плюс текст ошибки прямо на экране (там всё равно больше нечего
читать). След запуска:

```
[trace] startup: content manager initialised
[trace] startup: scene, camera and environment map ready
[trace] FileNotFoundException: Cannot locate resource ror.png in resource group FlagsRG.
[trace] ItemIdentityException: Unable to derive resource group for MyGUI_VP.glsles
        automatically since the resource was not found.
[fatal] RoRMain caught an Ogre::Exception and is returning: ItemIdentityException ...
[fatal] RoRMain is returning: the frame loop has ended.
```

Ни одной строки `frame N completed` — цикл кадров не запускался вовсе.

**Причина.** `MyGUI::OgreRenderManager::initialise()` в MyGUI 3.4.0 создаёт
GPU-программы с **зашитыми** именами и тут же загружает их из файла с тем же
именем:

```cpp
// Platforms/Ogre/OgrePlatform/src/MyGUI_OgreRenderManager.cpp:127-145
mVertexProgram = Ogre::HighLevelGpuProgramManager::getSingleton().createProgram(
    "MyGUI_VP." + shaderLanguage,          // shaderLanguage == "glsles"
    OgreDataManager::getInstance().getGroup(),
    shaderLanguage, Ogre::GPT_VERTEX_PROGRAM);
mVertexProgram->setSourceFile("MyGUI_VP." + shaderLanguage);
```

А в ресурсах RoR шейдеры называются иначе:

```
resources/mygui/MyGUI_Ogre_VP.glsles      <- как есть
resources/mygui/MyGUI_Ogre_FP.glsles      <- как есть
```

То есть MyGUI 3.4.0 ищет `MyGUI_VP.glsles`, а лежит `MyGUI_Ogre_VP.glsles`.
Группа ресурсов, которую RoR передаёт в `initialise()`, — это
`Ogre::ResourceGroupManager::AUTODETECT_RESOURCE_GROUP_NAME` (`"OgreAutodetect"`),
то есть «ищи во всех группах». Файла нет ни в одной — и
`ResourceGroupManager::findGroupContainingResource()` бросает
`ItemIdentityException`, который дальше и проваливает `RoRMain`.

**Почему на десктопе этого нет.** Тот же блок закрыт проверкой на
поддержку фиксированного конвейера:

```cpp
if (!mRenderSystem->getCapabilities()->hasCapability(Ogre::RSC_FIXED_FUNCTION)) { ... }
```

На десктопе OGRE о `RSC_FIXED_FUNCTION` отвечает «есть», и путь со шейдерами
пропускается целиком. Поэтому устаревшие имена файлов спокойно живут в RoR и
все платформы, кроме GLES2, о нём не знают. RoR требует MyGUI 3.4.0 по
`conanfile.py` (`self.requires("mygui/3.4.0@anotherfoxguy/stable")`) — то есть
совпадает версия, но не совпадают имена файлов: вьюпорту без камеры (§7.24) и
этому багу нужны разные платформенные условия, и оба они на iOS выполняются.

**Что нельзя было сделать.** Наивное исправление — переименовать файлы RoR.
Оно не работает: вершинные программы совпадают побайтово, но
`MyGUI_Ogre_FP.glsles` у RoR объявлен как `#version 300 es` (и использует `in` и
`texture()`), тогда как его же вершинная программа — `#version 100`. В одной
линкуемой программе смешивать версии GLSL ES нельзя, а контекст на iOS —
OpenGL ES 2.0, где `#version 300 es` вообще не компилируется. Взяты собственные
шейдеры MyGUI 3.4.0 (`Media/MyGUI_Media/`), где обе половины `#version 100`:
линкуется на ES2 и принимается ES3.

**Исправление** — `patch_mygui_resources()` в
`patches/ror/apply_ios_patches.py`: пишет `MyGUI_VP.glsles` и `MyGUI_FP.glsles`
в `resources/mygui/` (там же их упакует `zip_folder_resources` в
`rorres/mygui.zip`). Валидатор `valanchors.py` дополнительно сверяет оба файла
побайтово с MyGUI 3.4.0.

Второй, некритичный пункт того же запуска: `GUI_RepositorySelector` просит
`ror.png` у группы `FlagsRG`, а файл есть только в `resources/icons/` (группа
`IconsRG`) — на каждый запуск логировался `FileNotFoundException`. Простая копия
`resources/icons/ror.png` → `resources/flags/ror.png` убирает его.

**Главный вывод по процессу.** Это четвёртый случай подряд одной породы
(§7.22 — каталог `resources`, §7.23 — `OgreZip.cpp`, §7.24 — камера в
`layoutSubviews`), и все четыре были бы видны **в CI**, а не на телефоне, если
бы собирать проверял не только факт сборки, но и то, что в бандле лежит под
теми именами, под какими его ищут. Добавлен `patches/verify_ipa.py` и шаг
**Check the IPA really can start** — жёсткий (не `continue-on-error`), который
падает с `::error::`, если в `rorres/mygui.zip` нет нужных шейдеров, если
`skeleton.zip` отсутствует или если бандл снова несёт каталог `resources/`.
Проверка на старом IPA корректно ловит обе проблемы.

---

### 7.26. Краш на первом кадре ImGui: `kb->isKeyDown()` по нулевому OIS-клавиатуровому указателю

После починки MyGUI (§7.25) приложение впервые дошло до главного меню:

```
[trace] startup: GUI manager created
[trace] *** Loading OIS ***
[trace] [RoR|Startup|Input] iOS: using OIS's iPhone multitouch backend
[trace] [RoR|Startup|Input] iOS: multitouch device ready
[trace] startup: AngelScript ready; menu wallpaper set
[trace] startup: entering the main menu state
[crash] SIGSEGV - invalid memory access addr=0x0 pc=...(+0xfe65c) lr=...(+0xfe658)
```

Пользователь видел **чёрный экран и почти мгновенный краш** — и это ровно то,
что показывает лог: оболочка уже есть, но ни один кадр ещё не нарисован (цикл
кадров не запускался), а первая же попытка отрисовать кадр убивает процесс.

Строка `startup: complete; entering the frame loop` в логе отсутствует, то есть
падение происходит **до** цикла. Между breadcrumb'ом и крашем в `main.cpp` стоит
ровно один вызов:

```cpp
// Hack to properly init DearIMGUI integration - force rendering image
App::GetGuiManager()->LoadingWindow.SetProgress(100, "Hack", /*renderFrame=*/true);
```

`SetProgress(..., renderFrame=true)` → `NewImGuiFrame()`:

```cpp
void GUIManager::NewImGuiFrame(float dt)
{
    ImGuiIO& io = ImGui::GetIO();
    OIS::Keyboard* kb = App::GetInputEngine()->GetOisKeyboard();
    io.KeyCtrl  = kb->isKeyDown(OIS::KC_LCONTROL);   // kb == nullptr на iOS
```

**Смещение указывает ровно туда.** Обработчик сигнала печатает адреса смещениями
от базы бинарника, `ror_sym.py` их резолвит:

```
0xfe65c -> RoR::GUIManager::NewImGuiFrame(float), func_off=0x1000fe62c,
           off_in_sym=0x30, sym_size=0xa8
```

Краш воспроизводим дважды подряд на **одинаковом** смещении, значит не гонка.
`addr=0x0` (не `0x8` и не `0x1d`) — это загрузка виртуальной таблицы через
`this == nullptr`: `isKeyDown` — чисто виртуальный метод в `OIS::Keyboard`, так
что `kb->isKeyDown(...)` компилируется в `ldr x9, [x0]` с `x0 == 0`. `lr ==
pc-4` как раз и означает, что предыдущей командой был `bl` (возврат из
`GetInputEngine()`), а сразу после — разыменование.

**Почему указатель нулевой.** Это *наш собственный дизайн*, задокументированный
в `patch_inputengine_header`: бэкенд OIS для iPhone отдаёт только джойстик
(акселерометр) и мультитач-устройство — клавиатуры и мыши там физически нет,
`createInputObject(OISKeyboard)` бросает исключение, и `mKeyboard` остаётся
`nullptr`.

Практически вся остальная часть ввода на это уже была закрыта `patch_inputengine`:

| место | защита |
|---|---|
| `Capture()` | `if (mMultiTouch) mMultiTouch->capture();` |
| `windowResized()` | своя копия `mouseState` (вызывается из конструктора!) |
| `SetKeyboardListener()` / `SetMouseListener()` | слушатель запоминается в члене |
| `getMouseNormalizedScreenPos()` | своя копия `mouseState` |
| `InputEngine::isKeyDown()` | `if (!mKeyboard) return false;` |

Пропущен **единственный** вызывающий, который берёт голый `OIS::Keyboard*` мимо
`InputEngine` — `GUIManager::NewImGuiFrame`. Он и оказался первым, кто им
воспользовался, потому что ImGui-кадр запрашивается впервые именно здесь:
во время старта `LoadingWindow::SetProgress()` вообще не вызывается (в логе нет
ни одной строки `[RoR|PleaseWaitUI]`).

**Исправление** — `patch_gui_manager()` в `patches/ror/apply_ios_patches.py`:
три флага модификаторов читаются только если указатель ненулевой, иначе
остаются `false` — клавиатуры нет, модификаторов нет. Никакого `#ifdef`: проверка
бесплатна и одинакова для всех платформ. Якорь сверяется `valanchors.py` с
чистым upstream (учитывая, что комментарий в оригинале имеет пять пробелов).

**Вывод по процессу.** Пятый баг подряд одной породы, и опять он виден только на
устройстве. Здесь CI не помочь — это не содержимое бандла, а нулевой указатель в
рантайме. Что реально сократило итерацию: обработчик сигнала печатал `si_addr`,
`pc`, `lr` и `fp` смещениями от базы, а бинарник не стрипается — то есть по
одной строке из лога сразу понятно, в какой функции и на каком сдвиге упало.
Этот механизм окупился дважды (§7.24 и §7.26).

---

### 7.27. GLES2 не имеет фиксированного конвейера, а 240 из 273 материалов RoR написаны без шейдеров

Гард клавиатуры (§7.26) отработал: краша нет, и впервые дошли до **реальной
отрисовки**. Более того, шейдеры MyGUI из §7.25 компилируются именно так, как
задумано:

```
[trace] Vertex Program:MyGUI_VP.glsles Fragment Program:MyGUI_FP.glsles
[trace] GLSL compiling: MyGUI_VP.glsles
```

(предупреждения про «no matching vertex attributes» для `blendWeights`/`uv*` —
безобидные: OGRE пытается привязать стандартные атрибуты, которых у шейдера
MyGUI нет). То есть исправление §7.25 подтверждено от начала до конца.

Дальше первый же настоящий кадр упёрся в потолок всей графики:

```
[fatal] RoRMain caught an Ogre::Exception and is returning:
[trace] InvalidStateException: RenderSystem does not support FixedFunction, but technique
        of 'rigsofrods/WallpaperMat' has no Vertex Shader. Use the RTSS or write custom shaders.
        in SceneManager::_setPass at OgreSceneManager.cpp (line 913)
```

Опять чёрный экран без краша: `RoRMain` ловит исключение и возвращает 0.

**Почему это не локальная проблема.** Я выгрузил чистое апстримное дерево на
закреплённом SHA и посчитал материалы, у которых нет ни одного
`vertex_program_ref`: **240 из 273**. Это весь `OgreCore.material` (шрифты
`Core/OgreText`, панели оверлеев, профайлер), почти весь `ror.material`
(приборные панели, стрелки, маячки, колёса, флейры, вода), paged-трава и
деревья, все частицы, caelum, cubemaps, `managed/mesh_standard/*` и прочее.
Рукописные шейдеры для такого количества — не вариант, а модный контент
привозит материалы, которые мы вообще не можем патчить.

**Решение — RTShader System, как и советует сам текст ошибки.** RTSS читает
фиксированное описание материала (текстурные юниты, блендинг, alpha rejection,
туман) и на лету строит из него настоящие GLSL ES-программы.

Что пришлось выяснить, прежде чем трогать это (всё это в OGRE 1.11.6):

| Факт | Источник |
|---|---|
| В OGRE 1.11.6 есть **GLSL ES writer** — `OgreShaderGLSLESProgramWriter.cpp`, пишет `#version <native>` | репозиторий OGRE |
| Библиотеку функций он берёт из **`.glsl`** (`cacheDependencyFunctions` → `libName + ".glsl"`), а не `.glsles` | тот же файл |
| RoR уже пакует эти файлы: `resources/rtshader` → группа `RtShaderRG`, и имена функций совпадают с теми, что генератор порождает (`FFP_Transform`, `FFP_SampleTexture`, `FFP_Modulate`…) | локальное дерево |
| `OgreRTShaderSystem` уже собран и линкуется (`find_package(OGRE COMPONENTS … RTShaderSystem)`) | `cmake/DependenciesConfig.cmake` |
| RTSS **сам** произвольные материалы не конвертирует — только через `createShaderBasedTechnique()` или блок `rtshader_system` в скрипте | `OgreShaderGenerator.cpp` |
| `Ogre::OverlaySystem` (1.11) **не владеет** SceneManager — оверлеи рисует тот менеджер, на который он подписан как RenderQueueListener | `OgreOverlaySystem.h` |

**Ключевое свойство, на котором построен весь риск-менеджмент.**
`Material::getBestTechnique()` (OgreMaterial.cpp:357): если у материала нет
техники в активной схеме, OGRE сначала спрашивает слушателя
`MaterialManager::Listener::handleSchemeNotFound`, и **если тот вернул NULL —
откатывается на технику по умолчанию**. То есть переключение активной схемы на
`NGOS` физически не может сломать материалы, у которых шейдеры уже есть (MyGUI,
ImGui, шейдеры машин, ландшафт, небо): `createShaderBasedTechnique()` для
программируемого исходника возвращает `false`, мы возвращаем `nullptr`, и OGRE
рисует материал как прежде. Конвертируются **только** материалы без шейдеров.

Ещё два порядковых ограничения, которые надо соблюсти, иначе ничего не
заработает молча:

1. RTSS должен быть инициализирован **после** создания RenderSystem, но
   **до** инициализации групп ресурсов (он подписывается на их события) и
   **до** создания viewport — viewport при создании копирует активную схему
   материала, и если там окажется `General`, RTSS никогда не провалидирует
   свою схему. У нас вставка стоит сразу после `Root::initialise()`, а окно и
   viewport создаются в конце `SetUpRendering()`.
2. Каждый SceneManager надо отдельно зарегистрировать
   (`addSceneManager`), иначе для него нет ни валидации схемы, ни построения
   шейдеров. В RoR SceneManager один — `GfxScene::Init()`, и он же рисует
   оверлеи (main.cpp подписывает на него `OverlaySystem`), так что хватает
   одной точки.

**Что сделано** — `patch_rtshader()` в `apply_ios_patches.py`:

* `AppContext.cpp` — класс `IosFixedFunctionMaterialListener` (только для схемы
  `NGOS`, с защитой от повторного входа, потому что построение техники может
  снова спросить о материале) и включение RTSS сразу после
  `Root::initialise()`;
* `ios_platform.h` — объявление `RoR::Ios::RtssRegisterSceneManager`;
* `GfxScene.cpp` — регистрация SceneManager.

Построение техники принудительно запускается сразу
(`invalidateMaterial` + `validateScheme`): `preFindVisibleObjects` для этого
кадра уже прошёл, и без принуждения первый кадр отрисовался бы пустым.
Каждая конвертация пишется в лог устройства —
`[rtss] generating shaders for fixed-function material '...'`, — так что по
trace видно, что именно и сколько было построено.

`verify_ipa.py` теперь дополнительно требует в бинарнике строки
`RTShader System up, active material scheme` и в `rorres/rtshader.zip` — сам
`FFPLib_Transform.glsl`: без библиотеки генератор падает в момент генерации,
а не при сборке.

Ожидаем в trace: `[rtss] RTShader System up, active material scheme 'NGOS'`,
затем до двухсот с лишним строк `generating shaders…`, и главное — **отрисованные
обои главного меню**.

**Как это упало в CI и почему валидатор это не поймал.** Первый прогон
закончился ещё на шаге патчей:
`FileNotFoundError: ror/source/main/ios/ios_platform.h`. Патч объявляет свой
хук в `ios_platform.h`, а этот файл создаёт `patch_ios_platform`, которая в
`main()` шла **после**. `valanchors.py` при этом вызывала их уже в верном
порядке (он выверен ещё для GUIManager), так что локальная проверка прошла, а
CI упал. Вывод: порядок вызовов в `main()` и порядок вызовов в валидаторе —
одно и то же обязательство, и валидатор должен следовать за `main()`, а не
наоборот; комментарий на месте вызова теперь говорит, почему порядок нельзя
менять.

---

## Уточнённая карта порта

| Компонент | Судьба | Состояние |
|---|---|---|
| Собственно сборка под iOS arm64 | — | **собирается и линкуется** (§7.11) |
| Упаковка `.ipa` | — | шаги готовы, не проверены (§7.13) |
| Платформенный слой (`main`, цикл, плагины, размер окна) | — | код написан, не проверен (§7.12) |
| Отрисовка (OGRE 1.11.6) | оставить, включить штатный iOS-путь GLES2/EAGL | зависит только от старта |
| OIS (ввод) | собрать самим с iPhone-бэкендом; события касаний → события RoR | библиотека готова, слой ввода — нет |
| MyGUI (интерфейс) | оставить, подать в него события касаний | ждёт слоя ввода |
| PagedGeometry | собираем сами (сборочная система не для iOS), ставим в префикс | готово (§7.5) |
| Caelum | отключаем (`#ifdef USE_CAELUM`), небо рисуем средствами OGRE | — |
| Физика | чистый C++, должна собраться как есть | **компилируется** |
| Парсеры форматов | чистый C++, должны собраться как есть | **компилируется** |
| `main.cpp`, `PlatformUtils` | заменить платформенной обвязкой iOS | готово (§7.12) |
| Контент | упаковать в контейнер приложения | шаг готов (§7.13) |

Это **порт**, а не переписывание — то есть именно то, что было нужно.
