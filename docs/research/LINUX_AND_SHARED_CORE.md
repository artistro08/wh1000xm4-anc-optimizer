# Linux ve ortak DSP çekirdeği — araştırma notu

Tarih: 2026-10-25. Kapsam: kaynak incelemesi ve mimari plan; yeni kurulum veya ses cihazı testi yapılmadı.

## Karar önerisi

İlk çalışan hedef Linux/PipeWire olsun. Uygulamalar AeroSpatial sanal çıkışına gönderilir; DSP çıkışı açıkça seçilmiş fiziksel kulaklığa bağlanır. Giriş/çıkış yönlendirmesi, uygulama sesini yalnızca kaydedip tekrar çalmaktan farklıdır; çift ses ve döngü önlenmelidir. Varsayılan aygıtı değiştirmek tek başına mevcut bütün uygulama akışlarının taşındığını kanıtlamaz.

PipeWire'ın resmi filter-chain modülü sanal sink ve capture/playback akışlarını destekler. SOFA spatializer opsiyonu `libmysofa` ile derlenmiş kurulumlarda bulunur; bu, kendi motorumuz için yararlı bir karşılaştırma yolu olabilir. Ürün yolunda mevcut C++ motoru sürdürmek, platformlar arasında aynı işleme davranışını korumayı kolaylaştırır. Bu seçim bir tasarım önerisidir, karşılaştırmalı kalite testi sonucu değildir. [PipeWire filter-chain](https://docs.pipewire.org/page_module_filter_chain.html)

Steam Audio resmi kaynakları Windows, Linux ve Android desteği ile Apache-2.0 lisansını bildiriyor. Dolayısıyla ilk aday mevcut Steam Audio backend'idir; sıfırdan konvolüsyon motoru yazmak ilk hedef olmamalı. Kütüphane lisansı, ayrıca indirilecek HRTF veri kümelerinin yeniden dağıtım hakkını sağlamaz. [Steam Audio](https://github.com/ValveSoftware/steam-audio)

SOFA desteği bütün SOFA dosyalarını kapsamaz: motor `SimpleFreeFieldHRIR` ve ek alan kısıtları istiyor. Yüklemeden önce bu sınırlar kontrol edilmeli; uyumsuz dosya açık hata vermeli. [Steam Audio custom HRTFs](https://github.com/ValveSoftware/steam-audio/blob/master/core/doc/guide.rst)

## Mevcut koddan çıkan işler

- `aerospatial/CMakeLists.txt`, core içine `PipeWireBridge.cpp` ekliyor ve PipeWire'ı zorunlu bağlıyor. Platformdan bağımsız core, Linux adaptörü ve offline test yürütücüsü ayrı hedefler olmalı. İndirme scripti de yalnızca Linux x64 destekliyor.
- `processStereo` ve `processPointSource`, eksik frame'i sıfırla tamamlayıp kalan çıktıyı atıyor. Stateful giriş/çıkış FIFO'su veya eşdeğer doğru blok adaptörü gerekiyor.
- `SpatialConfig.mode` ve `enableEarlyReflections` mevcut işleme yolunda kullanılmıyor. `setOrbitalCoordinates` alanları güncelliyor fakat stereo yol bu alanları okumuyor. Bunlar hazır özellik olarak listelenmemeli.
- UI/control thread'inden parametre güncellemeleri için güvenli parametre aktarımı ve yumuşak geçiş gerekiyor. HRTF yüklemesi ses callback'i dışında yapılmalı.
- Sanal sink portları var; mevcut bridge açık hedef aygıt seçimi, otomatik bağlantı ve yeniden bağlanma politikasını tamamlamıyor. `wpctl` içinde node görünmesi uçtan uca ses kanıtı değildir.

## İlk kabul kontrolleri

1. Aynı girişin 64/128/256/480/512 ve karışık bloklarla işlenmesi, sabit blok referansına hizalandığında aynı örnek dizisini üretir; kuyruk ve filtre sonu korunur.
2. Spotify, tarayıcı ve yerel oynatıcıdan seçilmiş akışlar tek işlenmiş yoldan kulaklığa ulaşır; bypass, aygıt değişimi ve daemon kapanışı kontrol edilir.
3. 44,1/48 kHz, mono/stereo, giriş sessizliği, geçersiz SOFA ve kaynak değişimi açık davranışa sahiptir. Offline test, PipeWire veya ses aygıtı istemez.

Gecikme benchmark'ı kullanıcı isteğiyle kapsam dışıdır. Blok sürekliliği ve yönlendirme kontrolleri gecikme testi değil, temel doğruluk kontrolüdür.
