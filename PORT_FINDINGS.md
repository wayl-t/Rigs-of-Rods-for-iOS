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
- **CRLF** в `build_ipa.sh` — Windows-наследие, ломает bash на macOS. Лечится `.gitattributes` с `* text=auto eol=lf`.
- **Отсутствие флага `+x`** — git на Windows не хранит исполняемый бит, `./build_ipa.sh` падал с `Permission denied`. Лечится вызовом `bash build_ipa.sh`.

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
- Объём: ~180 000 строк C++.

### Обязательные зависимости (13)
`ogre3d 1.11.6.1`, `ois 1.4.1`, `mygui 3.4.0`, `ogre3d-caelum`,
`ogre3d-pagedgeometry`, `angelscript`, `openal-soft`, `socketw`,
`libcurl`, `fmt`, `rapidjson`, `discord-rpc`, `openssl`.

### Прогон 1: macOS
- Conan успешно **скачал 21 готовый бинарник** под macOS.
- Упало на сборке **OIS из исходников**:
  - `src/mac/CocoaJoyStick.mm:78` → `std::unary_function` **удалён в C++17**
  - `OISException.h:76` → макрос `OIS_EXCEPT` использует `throw(...)` — **удалено в C++17**
- Вывод: код OIS — из эпохи C++98 и несовместим с современным clang.

### Прогон 1: iOS
- Граф из **33 пакетов разрешился полностью**. Это хорошая новость сама по себе.
- Упало на `Invalid: 'settings.os.sdk' value not defined` — **это ошибка моего
  профиля Conan**, а не свойство RoR. Исправлено в вехе 2 добавлением
  `-s:h os.sdk=iphoneos`.

---

## Веха 2 — OGRE на iOS: КЛЮЧЕВОЙ ВОПРОС

Изначально я считал рендерер главной стеной. **Это оказалось неверно.**

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

EAGL — это нативный API iOS для контекста OpenGL ES. В OGRE он реализован
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

## Что всё ещё неизвестно / не проверено

- **Собирается ли OGRE 1.11.6 современным Xcode 16 / iOS SDK 18.5.** Код OGRE —
  примерно 2016 года. Как и у OIS, там наверняка есть места, отвалившиеся в
  новых стандартах C++ (`std::unary_function`, `throw(...)`, `auto_ptr`).
  Ожидается партия механических правок. Проверяется прогоном CI (веха 2).
- **Рецепт `ogre3d` в Conan под iOS.** Граф разрешается, но сборка под iOS
  ещё не подтверждена.
- **MyGUI (GUI) на iOS.** У RoR 78 файлов GUI на MyGUI. MyGUI — чистый C++ и
  рисуется через OGRE, но ему нужен поток событий ввода.
- **OIS на iOS неприменим в принципе.** Это настольная библиотека
  (клавиатура/мышь/джойстик) с бэкендами Win32/Linux/macOS-Cocoa. Для iOS у неё
  бэкенда нет вообще. На iPhone ввод — это касания, то есть OIS в любом случае
  выбрасывается и заменяется небольшим слоем.
- **Контент.** Машины и ландшафты RoR должны попасть в контейнер приложения
  (как в GTA V iOS кладут данные в Documents через Файлы).

---

## Уточнённая карта порта

| Компонент | Судьба |
|---|---|
| Отрисовка (OGRE 1.11.6) | оставить, включить штатный iOS-путь GLES2/EAGL |
| OIS (ввод) | выбросить, написать слой касаний на UIKit |
| MyGUI (интерфейс) | оставить, подать в него события касаний |
| Caelum, PagedGeometry | плагины OGRE, едут вместе с ним |
| Физика | чистый C++, должна собраться как есть |
| Парсеры форматов | чистый C++, должны собраться как есть |
| `main.cpp`, `PlatformUtils` | заменить платформенной обвязкой iOS |
| Контент | упаковать в контейнер приложения |

Это **порт**, а не переписывание — то есть именно то, что было нужно.

---

## ���� 3 � OGRE 1.11.6 ��� iOS: ��������

������� ����������� � ����� ������ ������������.

**Route 1** (������ CMake/toolchain/ios.toolchain.xcode.cmake, ������ �����)
- �� ����������������� �� ����������� CMake (��������������� cmake_minimum_required 3.3.0 � CMake 4.x)

**Route 2** (����������� CMAKE_SYSTEM_NAME=iOS + -DAPPLE_IOS=TRUE, Xcode ���������)  
- **������������: OK**
- **������:**

| ������ | ��������� |
|---|---|
| **OgreMain** | BUILD SUCCEEDED (0 ������) |
| **RenderSystem_GLES2** | BUILD SUCCEEDED (0 ������, ���� �������� ��precation) |
| **OgreOverlay** | ������ ����������� � ������������ ������� ������� |

**�������� �����:** OGRE 1.11.6 **������� ���������� ��� iOS** ����������� ���������������. ���� ��������� (OgreMain + GLES2/EAGL) ����. ��� ������� ������� ���������������.

������: ������������ �����������, ����������� ������������ ��� �������� ��������.

---

## ���� 4 � RoR �� macOS: ������� ���������

������� ������� RoR �� macOS (����� conan_provider + ninja). 
- ������� ������� Conan ����� conan profile update settings.compiler.cppstd=gnu14 �� ����� � ������ OIS ������ � gnu17 (� ���� � matrix), ��� ������������� ������������ clang �� macos-15.
- ������������ ������ OIS 1.4.1 �� macOS: std::unary_function (����� � C++17) + 	hrow(...) � ������� OIS_EXCEPT.
- ����� ���� �������������� � OIS.

**��� ��� ������:** ����� ������� RoR �� macOS, ����� ���� ���������� ��� OIS (������ unary_function, ��������� ����������), ���� ������� OIS � C++14 (�� ������� �� �������������� ����� �������). �������� ������ ���� ��� ���������� ���� � ���� ��������� ������ OIS (��������� ����), ���� �������� ������/�������� OIS ��� ������������ ������ *����������* ���� RoR.

��� iOS OIS **���������� � ��������** (������ Win32/Linux/macOS-Cocoa), ������� ��� �� ����� ������� ������ ����� touch-�����. ��������������, �������� �� ������� ����� �� ��������� ������ OIS �� macOS � ������ ����� ���� ��������� ROR_USE_ANGELSCRIPT/������, ���� ����������� ������� ��� OIS ����� �������� ���������� (���� ��������) ���� ����������� ����������� iOS-����.

�� ������� ���� �������: ���� OGRE 1.11.6 ��� iOS ���������� (OgreMain + RenderSystem_GLES2) � ��� ������� ������.
