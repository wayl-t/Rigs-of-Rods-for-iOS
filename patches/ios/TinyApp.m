// Diagnostic target only - never shipped as the game.
//
// The milestone-0 smoke test (a ~110 KB xcodebuild Hello World) installs on
// the device while the real RoR bundle does not, and the two differ in far
// more than one thing.  This program is the smallest possible iOS app, but it
// is packaged by exactly the same steps as RoR: the same Info.plist, the same
// PkgInfo, the same ad-hoc codesign invocation and the same Payload/ + zip
// construction.
//
// The split it gives is unambiguous:
//   * RoR-ios-tiny.ipa installs, RoR-ios-lite.ipa does not
//         -> the packaging, the plist and the signature are all fine, and the
//            problem lives in the RoR binary itself (size / structure).
//   * neither installs
//         -> the packaging or the signer is at fault, not the game binary.
#import <UIKit/UIKit.h>

@interface TinyAppDelegate : UIResponder <UIApplicationDelegate>
@property (nonatomic, strong) UIWindow *window;
@end

@implementation TinyAppDelegate

- (BOOL)application:(UIApplication *)application
        didFinishLaunchingWithOptions:(NSDictionary *)launchOptions {
    self.window = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
    UIViewController *controller = [[UIViewController alloc] init];
    controller.view.backgroundColor = [UIColor systemGreenColor];
    self.window.rootViewController = controller;
    [self.window makeKeyAndVisible];
    return YES;
}

@end

int main(int argc, char *argv[]) {
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil,
                                 NSStringFromClass([TinyAppDelegate class]));
    }
}
