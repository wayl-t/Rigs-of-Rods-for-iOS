import UIKit

/// Milestone 0 screen: proves the whole chain works end to end.
/// If you can read these numbers on your iPhone, then "GitHub Actions -> .ipa ->
/// SideStore -> iPhone" works, and everything after this is "just" porting.
final class MainViewController: UIViewController {

    private let titleLabel = UILabel()
    private let statusLabel = UILabel()
    private let detailLabel = UILabel()

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(red: 0.07, green: 0.07, blue: 0.09, alpha: 1)

        configure(titleLabel, size: 34, weight: .bold)
        configure(statusLabel, size: 20, weight: .semibold)
        configure(detailLabel, size: 15, weight: .regular)

        titleLabel.text = "Rigs of Rods"

        let selfTest = rorcore_self_test()
        if selfTest == 0 {
            statusLabel.text = "конвейер работает ✅"
            statusLabel.textColor = UIColor(red: 0.35, green: 0.85, blue: 0.45, alpha: 1)
        } else {
            statusLabel.text = "сбой ядра, код \(selfTest) ⛔️"
            statusLabel.textColor = UIColor(red: 0.95, green: 0.35, blue: 0.35, alpha: 1)
        }

        // Real value from the native core, computed on the device.
        let force = rorcore_beam_force(3.0, 2.5, 12000.0, 400.0, 0.0)
        detailLabel.text = """
        \(String(cString: rorcore_version()))
        beam force (3.0 -> 2.5 m): \(String(format: "%.0f", force)) N
        \(machineSummary())
        """

        let stack = UIStackView(arrangedSubviews: [titleLabel, statusLabel, detailLabel])
        stack.axis = .vertical
        stack.spacing = 14
        stack.alignment = .center
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)

        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            stack.leadingAnchor.constraint(greaterThanOrEqualTo: view.leadingAnchor, constant: 32),
            stack.trailingAnchor.constraint(lessThanOrEqualTo: view.trailingAnchor, constant: -32),
        ])
    }

    private func configure(_ label: UILabel, size: CGFloat, weight: UIFont.Weight) {
        label.font = UIFont.systemFont(ofSize: size, weight: weight)
        label.textColor = .white
        label.textAlignment = .center
        label.numberOfLines = 0
    }

    /// Shows the memory ceiling iOS actually grants this app. On a free Apple ID
    /// it is roughly half of the device RAM; the paid entitlement raises it.
    private func machineSummary() -> String {
        let gb = Double(ProcessInfo.processInfo.physicalMemory) / 1_073_741_824.0
        let limit = Double(rorcore_available_memory()) / 1_048_576.0
        return String(format: "RAM %.0f GB · лимит приложению %.0f MB", gb, limit)
    }
}
