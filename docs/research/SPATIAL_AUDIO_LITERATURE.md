# Stereo müzik için uzamsal kulaklık işleme: sınırlı literatür incelemesi

Tarih: 2026-10-25. Kapsam: Spotify benzeri iki kanallı sistem çıkışını iki sanal hoparlörden binaural olarak dinletmek. Bu inceleme uygulama veya gecikme ölçümü içermez. TinyFish erişimi ana çalışma sırasında kullanılamadığı için web aracı kullanıldı; arXiv MCP mevcut değildi. Dört web çağrısıyla sınırlı tarama yapıldı.

## Temel ayrım

Önerilen giriş sözleşmesi iki kanallı PCM'dir. L/R kanallarına sanal hoparlör konumları atamak, mevcut stereo miksin kulaklık sunumunu değiştirir. Özgün enstrüman nesnelerini, yükseklik kanallarını veya yapımcı tarafından belirlenmiş nesne konumlarını geri getirmez. Bu nedenle ürün adı/arayüzü “stereo sanallaştırma” demeli; nesne tabanlı veya gerçek çok kanallı kaynak desteği ayrıca tasarlanmalıdır. Bu, aşağıdaki deneylerin sonucu değil, girişteki bilginin ve önerilen mimarinin sınırıdır.

## Okunan birincil çalışmalar

### 1. Jayaram, Kemelmacher-Shlizerman ve Seitz (2023)

[HRTF Estimation in the Wild — arXiv:2311.03560](https://arxiv.org/abs/2311.03560), [yayın DOI](https://doi.org/10.1145/3586183.3606782).

HTML tam metninden §3 yöntem, §4 veri/eğitim, §5 sonuçlar ve §6 sınırlamalar okundu. Sekiz katılımcıda kişiselleştirilmiş spektral HRTF, genel HRTF'ye göre toplam açı/yükseklik hatasını ve ön–arka karışmasını iyileştirdi; azimut farkı anlamlı değildi. Faz/ITD genel HRTF'den alınmıştı. Çalışma tek, sabit ses kaynağı; kulak kanalı girişindeki mikrofonlar ve kafa yönü bilgisi gerektiriyor. Ticari kulaklığa doğrudan uygulanabilirlik kanıtlanmıyor. Müzik tınısı veya uzun süreli müzik tercihi testi değil.

**Çıkarım:** MVP'de otomatik kişisel HRTF çıkarımı vaat edilmemeli. İleride kullanıcı HRTF içe aktarma ve profil seçimi için yer bırakılmalı; genel bir profilin herkeste aynı dışsallaştırmayı sağlayacağı varsayılmamalı.

### 2. Hoppe, van de Par, Flanagin ve Ewert (2024)

[Combined assessment of auditory distance perception and externalization — arXiv:2408.14198](https://arxiv.org/abs/2408.14198), [PDF](https://arxiv.org/pdf/2408.14198).

PDF'de giriş (s. 1–5), yöntem koşullarının dönen kesitleri (s. 10–12), dışsallaştırma sonuç özeti (s. 22–23), tartışmanın başlangıcı ve sonuç maddeleri (s. 27–28, 34–35) okundu; 44 sayfanın tamamı okunmadı. On dinleyicili sanal görsel/işitsel ortam deneyinde yankılanma ve oda büyüklüğü dışsallaştırmayı anlamlı etkiledi. Kafa takibi/render türleri arasında eğilim vardı, fakat fark anlamlı değildi. Algılanan uzaklık ile dışsallaştırma kısmen bağımsızdı.

**Çıkarım:** “Daha uzak” ve “kafanın dışında” ayrı değerlendirmeler olmalı. Kafa takibi değerli bir genişleme adayıdır; bu çalışma evrensel fayda garantisi vermez. Oda katkısı ayarlanabilir olmalı. Bu deneyden müzik için ideal erken yansıma süresi, seviye veya tını kaybı çıkartılamaz.

### 3. Basha, Gupta ve Sharma (2007)

[Stereo widening system using binaural cues for headphones — konferans PDF](https://users.cecs.anu.edu.au/~ramtin/ICSPCS/ICSPCS%2707/papers/55.pdf).

Beş sayfalık PDF'nin ilk sayfasındaki giriş ve §II yöntem başlangıcı okundu; sonuçlar/deney ayrıntıları okunmadı. Yaklaşım fark sinyalini güçlendirme, çapraz besleme ve erken yansıma bileşenlerini birleştiriyor. Okunan bölüm tını renklenmesini değerlendirme ölçütü olarak anıyor. DOI/arXiv kaydı bu sınırlı taramada doğrulanmadı. Bu kaynak performans kanıtı olarak kullanılmıyor.

**Çıkarım:** Stereo genişletme, hoparlör sanallaştırması ve dışsallaştırma aynı ölçüt değildir. MVP'de fark sinyalini otomatik güçlendirmek yerine iki sanal hoparlörün binaural aktarımını açıkça modellemek daha denetlenebilir bir tasarım seçimidir.

## Somut MVP kararları (mühendislik önerisi)

1. İşleme: stereo PCM → ön tarafta iki sanal hoparlör → dört HRIR yolu ile 2×2 konvolüsyon → kulaklık stereo çıkışı. Başlangıç geometrisi ±30°; bunun optimum olduğu iddia edilmiyor. Sistem yakalama/çıkış katmanı platforma özel, DSP çekirdeği ortak tasarlanmalı.
2. Önce sabit yönlü sürüm. Kafa takibi sonraki aşamada dünyada sabit hoparlörler için dinleyiciye göre yönleri güncellemeli; profil/geometri değişimlerinde yumuşak geçiş düşünülmeli. XM4 sensör erişimi varsayılmamalı.
3. Genel HRTF başlangıç profili, ileride kişisel profil içe aktarma. Veri kümesinin ve kodun lisansları ayrı doğrulanmalı; makalenin erişilebilir olması dağıtım izni değildir.
4. Erken yansımalar ayrı ve düşük düzeyli deneysel kontrol; kapatılabilmeli. Hazır BRIR kullanılıyorsa içerdiği oda katkısının üzerine farkında olmadan ikinci oda eklenmemeli. Araştırma bu aşamada belirli bir süre/kazanç reçetesini doğrulamıyor.
5. Tınıyı koruma hedefi: ek bas/tiz veya side boost varsayılan olmasın; kazanç payı ve seviye eşlenmiş bypass karşılaştırması tasarlansın. HRTF'nin yönsel filtrelemesiyle gereksiz renklenme ayrılmalı; “frekans cevabı hiç değişmez” denmemeli. Kulaklık düzeltmesi ayrı, isteğe bağlı katman olmalı.
6. Sonraki dinleme değerlendirmesinde ayrı puanlar: dışsallaştırma, ön–arka karışması, vokal merkez kararlılığı, tını doğallığı ve tercih. Şu anda gecikme benchmark'ı veya kullanıcı testi yapılmadı.

## Kanıt sınırı

Bu üç kaynak, açık kaynak ve çapraz platform dağıtımın bütününü veya XM4 üzerinde Spotify müziğinde başarılı sonucu doğrulamaz. İki arXiv çalışmasında özetin ötesindeki bölümler okundu; üçüncü kaynak yalnız yöntem adayıdır. Erken yansıma/tını ödünleşimi için ek kontrollü müzik dinleme deneyi gerekir. PMC'deki kafa hareketi makalesi ve AES renklenme PDF'si erişim hatası verdiğinden tam metin okunmuş gibi değerlendirilmedi.
